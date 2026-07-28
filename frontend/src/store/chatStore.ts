import { create } from 'zustand'

import {
  api,
  ApiError,
  type AppConfig,
  type ConversationSummary,
  type Deployment,
  type Turn,
} from '../api/client'
import { streamChat } from '../api/stream'
import {
  DEFAULT_MAX_OUTPUT_TOKENS,
  DEFAULT_PROMPT,
  DEFAULT_REASONING_EFFORT,
  DEFAULT_REASONING_MODE,
  type ReasoningEffort,
  type ReasoningMode,
} from '../config/presets'

export interface Message {
  id: string
  role: 'user' | 'assistant'
  content: string
  timestamp?: string
  tokens?: number | null
  deployment?: string | null
  responseId?: string | null
  // Captured from the stream and preserved post-commit so the user can still
  // see how long the model thought + how much reasoning happened after the
  // streaming bubble is gone. Only meaningful for assistant messages.
  thinkingMs?: number | null
  reasoningChars?: number | null
  path?: 'responses' | 'chat' | null
}

interface StreamingState {
  content: string
  // Only the tail of the reasoning summary is kept in state (last
  // REASONING_KEEP_TAIL chars). The UI ticker shows the last ~500 chars
  // anyway, so retaining 60k+ chars per stream wastes memory and slows
  // every React diff. The total length is tracked separately so the
  // header can still display "60,539 ch".
  reasoning: string
  reasoningCharsTotal: number
  responseId: string | null
  path: 'responses' | 'chat' | null
  startedAt: number
  // Timestamp of the most recent stream event of any kind (start, delta,
  // reasoning_delta, fallback, etc.). The bubble surfaces "last update Xs
  // ago" so the user can tell whether the stream is alive or genuinely
  // stalled — independent of the elapsed timer.
  lastEventAt: number
  backgroundCancellable: boolean
  fallback?: { from: string; to: string; reason: string }
  retrying?: {
    attempt: number
    maxAttempts: number
    delaySeconds: number
  }
}

// Cap on the in-memory reasoning tail. ~3x what the UI shows, so brief
// re-layout flickers never reveal an empty box.
const REASONING_KEEP_TAIL = 1500

interface ToastState {
  kind: 'info' | 'error' | 'success'
  message: string
}

type LastErrorState =
  | {
      kind: 'error'
      message: string
      retryPrompt: string
    }
  | {
      kind: 'incomplete'
      message: string
      continuePrompt: string
      conversationId: string | null
      responseId: string
      deployment: string
    }

export interface ChatParams {
  systemPrompt: string
  reasoningEffort: ReasoningEffort
  reasoningMode: ReasoningMode
  maxOutputTokens: number
}

const PARAMS_STORAGE_KEY = 'aoai-chat:params'
const HISTORY_STORAGE_KEY = 'aoai-chat:prompt-history'
const HISTORY_MAX = 200

function loadHistory(): string[] {
  try {
    const raw = typeof localStorage !== 'undefined' && localStorage.getItem(HISTORY_STORAGE_KEY)
    if (raw) {
      const parsed = JSON.parse(raw)
      if (Array.isArray(parsed)) return parsed.filter((x): x is string => typeof x === 'string')
    }
  } catch {
    /* ignore */
  }
  return []
}

function saveHistory(h: string[]) {
  try {
    localStorage.setItem(HISTORY_STORAGE_KEY, JSON.stringify(h))
  } catch {
    /* ignore */
  }
}

const DEFAULT_PARAMS: ChatParams = {
  systemPrompt: DEFAULT_PROMPT,
  reasoningEffort: DEFAULT_REASONING_EFFORT,
  reasoningMode: DEFAULT_REASONING_MODE,
  maxOutputTokens: DEFAULT_MAX_OUTPUT_TOKENS,
}

function loadParams(): ChatParams {
  try {
    const raw = typeof localStorage !== 'undefined' && localStorage.getItem(PARAMS_STORAGE_KEY)
    if (raw) return { ...DEFAULT_PARAMS, ...JSON.parse(raw) }
  } catch {
    /* ignore */
  }
  return DEFAULT_PARAMS
}

function saveParams(p: ChatParams) {
  try {
    localStorage.setItem(PARAMS_STORAGE_KEY, JSON.stringify(p))
  } catch {
    /* ignore */
  }
}

export interface ChatStore {
  // server state
  config: AppConfig | null
  deployments: Deployment[]
  conversations: ConversationSummary[]
  activeId: string | null
  activeMessages: Message[]
  activeLastResponseId: string | null

  // ui state
  selectedDeployment: string
  streaming: StreamingState | null
  toast: ToastState | null
  settingsOpen: boolean
  parametersOpen: boolean
  params: ChatParams
  promptHistory: string[]
  // Persistent error surface — shows up as an inline card below the chat
  // and survives until the user retries, dismisses, or sends a new prompt
  // that streams content. Avoids the "toast auto-dismissed before I noticed"
  // failure mode.
  lastError: LastErrorState | null

  // actions
  bootstrap: () => Promise<void>
  refreshDeployments: () => Promise<void>
  refreshConversations: () => Promise<void>
  newConversation: () => void
  selectConversation: (id: string) => Promise<void>
  deleteConversation: (id: string) => Promise<void>
  setDeployment: (name: string) => void
  sendMessage: (content: string, opts?: { skipUserMessage?: boolean }) => Promise<void>
  putConfig: (patch: Partial<AppConfig>) => Promise<void>
  addDeployment: (name: string) => Promise<void>
  removeDeployment: (name: string) => Promise<void>
  openSettings: (open: boolean) => void
  openParameters: (open: boolean) => void
  setParams: (patch: Partial<ChatParams>) => void
  resetParams: () => void
  pushPromptHistory: (text: string) => void
  setToast: (t: ToastState | null) => void
  clearLastError: () => void
  retryLastError: () => void
  continueLastIncomplete: () => void
  stopStream: () => void
}

function newId() {
  return Math.random().toString(36).slice(2) + Date.now().toString(36)
}

// Module-scoped — only one stream runs at a time and we don't want this
// in store state (it would trigger React renders we don't need).
let currentAbortController: AbortController | null = null
let currentStopRequested = false
let currentStopGraceTimer: ReturnType<typeof setTimeout> | null = null

function cancelRemoteResponse(
  responseId: string | null | undefined,
  onError: (error: Error) => void,
) {
  if (!responseId) return
  void api.cancelResponse(responseId).catch((error: Error) => onError(error))
}

function requestCurrentStreamStop(
  responseId: string | null | undefined,
  backgroundCancellable: boolean,
  onError: (error: Error) => void,
) {
  currentStopRequested = true
  if (!backgroundCancellable) {
    currentAbortController?.abort()
    return
  }
  if (responseId) {
    cancelRemoteResponse(responseId, onError)
    currentAbortController?.abort()
    return
  }
  if (!currentStopGraceTimer) {
    currentStopGraceTimer = setTimeout(() => {
      currentAbortController?.abort()
    }, 30_000)
  }
}

// "Stale" watchdog: trip when no SSE event arrives for this long. Now
// includes `keepalive` events forwarded from the backend, so this only
// fires for genuinely dead connections. The freshness dot still goes
// rose at 90s of silence so the user sees something is off well before
// the auto-abort.
const STALE_STREAM_TIMEOUT_MS = 30 * 60 * 1000

// "No-content" watchdog: trip when total elapsed exceeds this AND no
// `output_text` delta has arrived yet. Catches the gpt-5.4-pro-on-xhigh
// runaway-reasoning case where the model never reaches output. Generous
// upper bound — 60 min covers all but the most extreme research prompts.
const NO_CONTENT_TIMEOUT_MS = 60 * 60 * 1000

function turnsToMessages(turns: Turn[]): Message[] {
  return turns
    .filter((t) => t.role === 'user' || t.role === 'assistant')
    .map((t) => ({
      id: newId(),
      role: t.role as 'user' | 'assistant',
      content: t.content,
      timestamp: t.timestamp,
      tokens: t.tokens,
      deployment: t.deployment,
      responseId: t.response_id,
    }))
}

export const useChatStore = create<ChatStore>((set, get) => ({
  config: null,
  deployments: [],
  conversations: [],
  activeId: null,
  activeMessages: [],
  activeLastResponseId: null,
  selectedDeployment: '',
  streaming: null,
  toast: null,
  settingsOpen: false,
  parametersOpen: false,
  params: loadParams(),
  promptHistory: loadHistory(),
  lastError: null,

  bootstrap: async () => {
    try {
      const [cfg, deps, convs] = await Promise.all([
        api.getConfig(),
        api.listDeployments(),
        api.listConversations(),
      ])
      const selected =
        cfg.default_deployment ||
        deps.default ||
        deps.data[0]?.id ||
        ''
      set({
        config: cfg,
        deployments: deps.data,
        conversations: convs.data,
        selectedDeployment: selected,
        settingsOpen: !cfg.configured,
      })
    } catch (e) {
      set({ toast: { kind: 'error', message: `Bootstrap failed: ${(e as Error).message}` } })
    }
  },

  refreshDeployments: async () => {
    const deps = await api.listDeployments()
    set({ deployments: deps.data })
  },

  refreshConversations: async () => {
    const convs = await api.listConversations()
    set({ conversations: convs.data })
  },

  newConversation: () => {
    if (get().streaming) {
      set({
        toast: {
          kind: 'info',
          message: 'Stop generation before starting a new chat.',
        },
      })
      return
    }
    set({
      activeId: null,
      activeMessages: [],
      activeLastResponseId: null,
      streaming: null,
      lastError: null,
    })
  },

  selectConversation: async (id) => {
    if (get().streaming) {
      set({
        toast: {
          kind: 'info',
          message: 'Stop generation before switching conversations.',
        },
      })
      return
    }
    try {
      const detail = await api.getConversation(id)
      set({
        activeId: detail.id,
        activeMessages: turnsToMessages(detail.turns),
        activeLastResponseId: detail.response_id,
        streaming: null,
        lastError: null,
      })
    } catch (e) {
      set({ toast: { kind: 'error', message: `Load failed: ${(e as Error).message}` } })
    }
  },

  deleteConversation: async (id) => {
    if (get().streaming) {
      set({
        toast: {
          kind: 'info',
          message: 'Stop generation before deleting a conversation.',
        },
      })
      return
    }
    try {
      await api.deleteConversation(id)
      const wasActive = get().activeId === id
      await get().refreshConversations()
      if (wasActive) get().newConversation()
    } catch (e) {
      set({ toast: { kind: 'error', message: `Delete failed: ${(e as Error).message}` } })
    }
  },

  setDeployment: (name) =>
    set((state) => ({
      selectedDeployment: name,
      lastError:
        state.lastError?.kind === 'incomplete' ? null : state.lastError,
    })),

  openSettings: (open) => set({ settingsOpen: open }),
  openParameters: (open) => set({ parametersOpen: open }),
  setParams: (patch) => {
    const next = { ...get().params, ...patch }
    saveParams(next)
    set({ params: next })
  },
  resetParams: () => {
    saveParams(DEFAULT_PARAMS)
    set({ params: DEFAULT_PARAMS })
  },
  pushPromptHistory: (text) => {
    const trimmed = text.trim()
    if (!trimmed) return
    const current = get().promptHistory
    // Skip if identical to the most recent entry (terminal-style dedupe).
    if (current[current.length - 1] === trimmed) return
    const next = current.length >= HISTORY_MAX
      ? [...current.slice(current.length - HISTORY_MAX + 1), trimmed]
      : [...current, trimmed]
    saveHistory(next)
    set({ promptHistory: next })
  },
  setToast: (t) => set({ toast: t }),
  clearLastError: () => set({ lastError: null }),
  retryLastError: () => {
    const err = get().lastError
    if (!err || err.kind !== 'error') return
    set({ lastError: null })
    // Re-run the same prompt but DON'T append a duplicate user bubble —
    // the original is already in `activeMessages`. Previous behaviour
    // ended up showing 3 copies of "Let's start from the very basic…"
    // after two Retry clicks.
    void get().sendMessage(err.retryPrompt, { skipUserMessage: true })
  },
  continueLastIncomplete: () => {
    const err = get().lastError
    if (!err || err.kind !== 'incomplete') return
    const state = get()
    if (
      state.activeId !== err.conversationId
      || state.activeLastResponseId !== err.responseId
      || state.selectedDeployment !== err.deployment
    ) {
      set({
        lastError: null,
        toast: {
          kind: 'error',
          message:
            'The incomplete response belongs to a different conversation or deployment.',
        },
      })
      return
    }
    set({ lastError: null })
    void get().sendMessage(err.continuePrompt)
  },
  stopStream: () => {
    const streaming = get().streaming
    requestCurrentStreamStop(
      streaming?.responseId,
      streaming?.backgroundCancellable ?? false,
      (error) => {
        set({
          toast: {
            kind: 'error',
            message: `Local stream stopped, but Azure cancellation failed: ${error.message}`,
          },
        })
      },
    )
  },

  putConfig: async (patch) => {
    const cfg = await api.putConfig(patch)
    set({ config: cfg })
    if (cfg.configured) set({ settingsOpen: false })
  },

  addDeployment: async (name) => {
    const r = await api.addDeployment(name)
    set({ deployments: r.data })
    if (!get().selectedDeployment) set({ selectedDeployment: name })
  },

  removeDeployment: async (name) => {
    const r = await api.removeDeployment(name)
    set({ deployments: r.data })
    if (get().selectedDeployment === name) {
      set({ selectedDeployment: r.data[0]?.id || '' })
    }
  },

  sendMessage: async (content: string, opts?: { skipUserMessage?: boolean }) => {
    if (get().streaming) {
      set({
        toast: {
          kind: 'info',
          message: 'A response is already streaming.',
        },
      })
      return
    }
    const {
      selectedDeployment,
      activeId,
      activeLastResponseId,
      activeMessages,
      params,
      deployments,
    } = get()
    if (!selectedDeployment) {
      set({ toast: { kind: 'error', message: 'Pick a deployment first.' } })
      return
    }
    if (!content.trim()) return

    const deployment = deployments.find((item) => item.id === selectedDeployment)
    const reasoningEffort = deployment?.reasoning_efforts.includes(params.reasoningEffort)
      ? params.reasoningEffort
      : deployment?.reasoning_efforts.includes('medium')
        ? 'medium'
        : deployment?.reasoning_efforts[0] ?? null
    const reasoningMode =
      params.reasoningMode === 'pro' && deployment?.reasoning_modes.includes('pro')
        ? 'pro'
        : null
    const maxOutputTokens = Math.min(
      params.maxOutputTokens,
      deployment?.max_output_tokens ?? params.maxOutputTokens,
    )
    let backgroundCancellable =
      deployment?.supports_responses_api === true &&
      (reasoningMode === 'pro' ||
        reasoningEffort === 'high' ||
        reasoningEffort === 'xhigh' ||
        reasoningEffort === 'max')

    const startedAt = Date.now()
    if (opts?.skipUserMessage) {
      // Retry path — the user message is already in activeMessages from the
      // failed attempt. Just kick off a fresh stream and clear lastError.
      set({
        streaming: {
          content: '',
          reasoning: '',
          reasoningCharsTotal: 0,
          responseId: null,
          path: null,
          startedAt,
          lastEventAt: startedAt,
          backgroundCancellable,
        },
        lastError: null,
      })
    } else {
      const userMsg: Message = {
        id: newId(),
        role: 'user',
        content,
        timestamp: new Date().toISOString(),
      }
      // Push to terminal-style history before the network call so up-arrow
      // recall works even if the request fails.
      get().pushPromptHistory(content)
      set({
        activeMessages: [...activeMessages, userMsg],
        streaming: {
          content: '',
          reasoning: '',
          reasoningCharsTotal: 0,
          responseId: null,
          path: null,
          startedAt,
          lastEventAt: startedAt,
          backgroundCancellable,
        },
        lastError: null,
      })
    }

    // ---- rAF-batched streaming state writes ---------------------------------
    // The model emits delta events at >100/sec during a fast stream. Calling
    // set() per event triggered a React render per token — visible lag.
    // We coalesce all pending updates into a single set() per animation frame
    // (60fps cap) so the UI never falls behind the GC or compositor.
    let pendingStreaming: StreamingState | null = null
    let rafScheduled = false
    let rafId: number | null = null
    const flushStreaming = () => {
      rafScheduled = false
      rafId = null
      if (pendingStreaming) {
        set({ streaming: pendingStreaming })
        pendingStreaming = null
      }
    }
    const scheduleStreaming = (next: StreamingState) => {
      pendingStreaming = next
      if (!rafScheduled) {
        rafScheduled = true
        if (typeof requestAnimationFrame === 'undefined') {
          flushStreaming()
        } else {
          rafId = requestAnimationFrame(flushStreaming)
        }
      }
    }

    // Watchdog state lives in function scope (not try-scope) so the
    // catch and finally blocks below can read them.
    //   1. STALE — no events at all for STALE_STREAM_TIMEOUT_MS → abort
    //   2. NO-CONTENT — total elapsed exceeds NO_CONTENT_TIMEOUT_MS and
    //      the model still hasn't emitted any output_text → abort
    // The first catches dead connections; the second catches the
    // model-is-still-reasoning-after-an-eternity failure mode that the
    // stale watchdog can't see (because reasoning_delta events keep
    // resetting it).
    let responseId: string | null = null
    let incomplete:
      | {
          reason: string
          usage: { total_tokens?: number } | null
        }
      | null = null
    let watchdogReason: null | 'stale' | 'no_content' = null
    let firstContentSeen = false
    let staleTimer: ReturnType<typeof setTimeout> | null = null
    let noContentTimer: ReturnType<typeof setTimeout> | null = null
    const armStaleTimer = () => {
      if (staleTimer) clearTimeout(staleTimer)
      staleTimer = setTimeout(() => {
        watchdogReason = 'stale'
        requestCurrentStreamStop(responseId, backgroundCancellable, (error) => {
          set({
            toast: {
              kind: 'error',
              message: `Azure cancellation failed after stream stall: ${error.message}`,
            },
          })
        })
      }, STALE_STREAM_TIMEOUT_MS)
    }

    try {
      let path: 'responses' | 'chat' | null = null
      let conversationId = activeId
      let assembled = ''
      // Reasoning: keep only the tail in memory; track total separately so
      // the header counter stays honest even as old chars are dropped.
      let reasoningTail = ''
      let reasoningCharsTotal = 0

      // Make a fresh AbortController per stream so the Stop button can
      // cancel only the *current* stream (not any future one).
      currentStopRequested = false
      if (currentStopGraceTimer) clearTimeout(currentStopGraceTimer)
      currentStopGraceTimer = null
      currentAbortController = new AbortController()
      const signal = currentAbortController.signal

      // Arm both watchdogs now that the AbortController exists.
      armStaleTimer()
      noContentTimer = setTimeout(() => {
        if (!firstContentSeen) {
          watchdogReason = 'no_content'
          requestCurrentStreamStop(responseId, backgroundCancellable, (error) => {
            set({
              toast: {
                kind: 'error',
                message: `Azure cancellation failed after reasoning timeout: ${error.message}`,
              },
            })
          })
        }
      }, NO_CONTENT_TIMEOUT_MS)

      for await (const evt of streamChat({
        deployment: selectedDeployment,
        content,
        conversation_id: activeId,
        previous_response_id: activeLastResponseId,
        instructions: params.systemPrompt.trim() || null,
        reasoning_effort: reasoningEffort,
        reasoning_mode: reasoningMode,
        max_output_tokens: maxOutputTokens,
      }, signal)) {
        armStaleTimer() // any event resets the watchdog
        if (evt.event === 'meta') {
          conversationId = evt.data?.conversation_id ?? conversationId
          set({ activeId: conversationId })
        } else if (evt.event === 'start') {
          responseId = evt.data?.response_id ?? null
          path = evt.data?.path ?? null
          if (path === 'chat') backgroundCancellable = false
          if (currentStopRequested) {
            if (currentStopGraceTimer) clearTimeout(currentStopGraceTimer)
            currentStopGraceTimer = null
            if (backgroundCancellable) {
              cancelRemoteResponse(responseId, (error) => {
                set({
                  toast: {
                    kind: 'error',
                    message: `Local stream stopped, but Azure cancellation failed: ${error.message}`,
                  },
                })
              })
            }
            currentAbortController?.abort()
            continue
          }
          // 'start' is a one-shot — flush immediately so user sees the path
          // badge appear without waiting for the next delta to schedule a frame.
          set({
            streaming: {
              content: '',
              reasoning: reasoningTail,
              reasoningCharsTotal,
              responseId,
              path,
              startedAt,
              lastEventAt: Date.now(),
              backgroundCancellable,
            },
          })
        } else if (evt.event === 'delta') {
          firstContentSeen = true
          assembled += evt.data?.text ?? ''
          scheduleStreaming({
            content: assembled,
            reasoning: reasoningTail,
            reasoningCharsTotal,
            responseId,
            path,
            startedAt,
            lastEventAt: Date.now(),
            backgroundCancellable,
          })
        } else if (evt.event === 'reasoning_delta') {
          const chunk = (evt.data?.text ?? '') as string
          reasoningCharsTotal += chunk.length
          // Append + trim to the tail window. Older chars are GC'd.
          reasoningTail = (reasoningTail + chunk).slice(-REASONING_KEEP_TAIL)
          scheduleStreaming({
            content: assembled,
            reasoning: reasoningTail,
            reasoningCharsTotal,
            responseId,
            path,
            startedAt,
            lastEventAt: Date.now(),
            backgroundCancellable,
          })
        } else if (evt.event === 'retrying') {
          responseId = null
          assembled = ''
          reasoningTail = ''
          reasoningCharsTotal = 0
          firstContentSeen = false
          set((s) =>
            s.streaming
              ? {
                  streaming: {
                    ...s.streaming,
                    content: '',
                    reasoning: '',
                    reasoningCharsTotal: 0,
                    responseId: null,
                    backgroundCancellable: false,
                    retrying: {
                      attempt: evt.data?.attempt ?? 2,
                      maxAttempts: evt.data?.max_attempts ?? 3,
                      delaySeconds: evt.data?.delay_seconds ?? 0,
                    },
                    lastEventAt: Date.now(),
                  },
                }
              : {},
          )
        } else if (evt.event === 'fallback') {
          backgroundCancellable = false
          set((s) =>
            s.streaming
              ? {
                  streaming: {
                    ...s.streaming,
                    backgroundCancellable: false,
                    fallback: evt.data,
                    lastEventAt: Date.now(),
                  },
                }
              : {},
          )
          if (currentStopRequested) {
            if (currentStopGraceTimer) clearTimeout(currentStopGraceTimer)
            currentStopGraceTimer = null
            currentAbortController?.abort()
          }
        } else if (evt.event === 'done') {
          responseId = evt.data?.response_id ?? responseId
          path = evt.data?.path ?? path
        } else if (evt.event === 'incomplete') {
          responseId = evt.data?.response_id ?? responseId
          path = evt.data?.path ?? path
          incomplete = {
            reason: evt.data?.reason || 'Response incomplete',
            usage: evt.data?.usage ?? null,
          }
        } else if (evt.event === 'saved') {
          await get().refreshConversations()
        } else if (evt.event === 'keepalive') {
          // Connection alive but no model progress. Update lastEventAt so
          // the freshness dot stays green and the stale watchdog (which
          // was just reset by armStaleTimer() at the top of the loop)
          // doesn't fire. Don't touch any other streaming state.
          set((s) =>
            s.streaming
              ? { streaming: { ...s.streaming, lastEventAt: Date.now() } }
              : {},
          )
        } else if (evt.event === 'error') {
          throw new Error(evt.data?.message || evt.data?.error || 'stream error')
        }
      }

      // Flush any pending streaming update before committing — otherwise the
      // final scheduled write could fire AFTER we set streaming to null and
      // re-show the streaming bubble after the message commits.
      pendingStreaming = null

      if (incomplete) {
        const terminal = incomplete
        if (!responseId) {
          set({
            streaming: null,
            lastError: {
              kind: 'error',
              message:
                'The response stopped without a response ID, so it cannot be continued safely.',
              retryPrompt: content,
            },
          })
          return
        }
        const incompleteResponseId = responseId
        const partialAssistant: Message | null = assembled.trim()
          ? {
              id: newId(),
              role: 'assistant',
              content: assembled,
              timestamp: new Date().toISOString(),
              tokens: terminal.usage?.total_tokens ?? null,
              deployment: selectedDeployment,
              responseId: incompleteResponseId,
              thinkingMs: Date.now() - startedAt,
              reasoningChars: reasoningCharsTotal,
              path,
            }
          : null
        set((s) => ({
          activeMessages: partialAssistant
            ? [...s.activeMessages, partialAssistant]
            : s.activeMessages,
          activeLastResponseId: incompleteResponseId,
          streaming: null,
          lastError: {
            kind: 'incomplete',
            message:
              terminal.reason === 'max_output_tokens'
                ? 'The response used its full output-token budget. The partial answer was saved; continue with a fresh budget.'
                : `The response stopped early (${terminal.reason}). The partial answer was saved.`,
            continuePrompt:
              'Continue exactly where you stopped. Do not repeat prior material.',
            conversationId,
            responseId: incompleteResponseId,
            deployment: selectedDeployment,
          },
        }))
        return
      }

      // Empty-response guard. The Responses API can `completed` a stream
      // with zero output_text deltas (max-token cut-off, content filter,
      // model decided not to answer). Don't commit a ghost bubble; raise a
      // persistent inline error so the user sees it (toasts auto-dismiss).
      if (!assembled.trim()) {
        const reasonedAt = reasoningCharsTotal > 0
        const msg = reasonedAt
          ? `Model reasoned (${reasoningCharsTotal.toLocaleString()} ch) but produced no output. Likely cause: the reasoning budget consumed the entire max_output_tokens cap. Open Parameters and raise Max Output Tokens (try 65,536 or higher), or lower the reasoning effort, then Retry.`
          : 'Empty response. The model may have been content-filtered, hit a token limit, or the connection closed early.'
        set({
          streaming: null,
          lastError: { kind: 'error', message: msg, retryPrompt: content },
        })
        return
      }

      // Commit assistant message to the list and clear streaming state.
      const assistantMsg: Message = {
        id: newId(),
        role: 'assistant',
        content: assembled,
        timestamp: new Date().toISOString(),
        deployment: selectedDeployment,
        responseId,
        thinkingMs: Date.now() - startedAt,
        reasoningChars: reasoningCharsTotal,
        path,
      }
      set((s) => ({
        activeMessages: [...s.activeMessages, assistantMsg],
        activeLastResponseId: responseId ?? s.activeLastResponseId,
        streaming: null,
      }))
    } catch (e) {
      // Four exit paths:
      //   1. STALE watchdog → "no events for 5 min" error with Retry
      //   2. NO-CONTENT watchdog → "reasoning for 20 min, no output yet"
      //      error with hint to lower effort
      //   3. User clicked Stop → clean cancel, no error card
      //   4. Real error (network, parse, etc.) → standard error card
      const isAbort =
        (currentAbortController?.signal.aborted ?? false) ||
        (e instanceof DOMException && e.name === 'AbortError') ||
        ((e as Error)?.name === 'AbortError')
      if (watchdogReason === 'stale') {
        set({
          streaming: null,
          lastError: {
            kind: 'error',
            message:
              `Stream stalled — no events for ${STALE_STREAM_TIMEOUT_MS / 60_000} min. ` +
              'The connection or Azure-side request hung. Retry to try again.',
            retryPrompt: content,
          },
        })
      } else if (watchdogReason === 'no_content') {
        set({
          streaming: null,
          lastError: {
            kind: 'error',
            message:
              `Model has been reasoning for ${NO_CONTENT_TIMEOUT_MS / 60_000} min without producing any output. ` +
              `This is the "runaway thinking" failure mode that hits gpt-5.4-pro on xhigh effort. ` +
              `Retry with a lower reasoning effort (medium or high) — usually that's enough to break the loop.`,
            retryPrompt: content,
          },
        })
      } else if (isAbort) {
        set({ streaming: null })
      } else {
        const msg = e instanceof ApiError ? e.message : (e as Error).message
        set({
          streaming: null,
          lastError: {
            kind: 'error',
            message: msg,
            retryPrompt: content,
          },
        })
      }
    } finally {
      pendingStreaming = null
      if (rafId !== null && typeof cancelAnimationFrame !== 'undefined') {
        cancelAnimationFrame(rafId)
      }
      rafId = null
      rafScheduled = false
      if (staleTimer) clearTimeout(staleTimer)
      if (noContentTimer) clearTimeout(noContentTimer)
      if (currentStopGraceTimer) clearTimeout(currentStopGraceTimer)
      currentStopGraceTimer = null
      currentStopRequested = false
      currentAbortController = null
    }
  },
}))
