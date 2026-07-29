/**
 * Server-Sent Events consumer for POST /api/chat.
 *
 * Native EventSource is GET-only; we POST a JSON body so we hand-parse the
 * SSE stream off `fetch` + `ReadableStream`.
 */
import type { ReasoningEffort, ReasoningMode } from '../config/presets'

export interface ChatStreamEvent {
  event: string
  data: any
}

export interface ChatStreamRequest {
  deployment: string
  content: string
  conversation_id?: string | null
  previous_response_id?: string | null
  history?: { role: string; content: string }[]
  instructions?: string | null
  reasoning_effort?: ReasoningEffort | null
  reasoning_mode?: Exclude<ReasoningMode, 'standard'> | null
  max_output_tokens?: number | null
}

export async function* streamChat(
  req: ChatStreamRequest,
  signal?: AbortSignal,
): AsyncGenerator<ChatStreamEvent> {
  const resp = await fetch('/api/chat', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      Accept: 'text/event-stream',
    },
    body: JSON.stringify(req),
    signal,
  })

  if (!resp.ok || !resp.body) {
    const body = await resp.text().catch(() => '')
    throw new Error(`POST /api/chat failed: ${resp.status} ${body.slice(0, 200)}`)
  }

  const reader = resp.body.getReader()
  const decoder = new TextDecoder('utf-8')
  let buffer = ''
  let currentEvent = 'message'

  try {
    while (true) {
      const { value, done } = await reader.read()
      if (done) break
      buffer += decoder.decode(value, { stream: true })

      // Process complete lines; SSE messages are separated by blank lines but
      // we yield per data: line, which is sufficient for our protocol.
      let nl
      while ((nl = buffer.indexOf('\n')) >= 0) {
        const raw = buffer.slice(0, nl)
        buffer = buffer.slice(nl + 1)
        const line = raw.replace(/\r$/, '')
        if (!line) {
          // event boundary; nothing to do because we emit on `data:` directly
          continue
        }
        if (line.startsWith('event:')) {
          currentEvent = line.slice(6).trim()
        } else if (line.startsWith('data:')) {
          const dataStr = line.slice(5).trim()
          if (!dataStr) continue
          let parsed: any = dataStr
          try {
            parsed = JSON.parse(dataStr)
          } catch {
            /* leave as string */
          }
          yield { event: currentEvent, data: parsed }
        }
        // ignore other field types (id:, retry:, comments)
      }
    }
  } finally {
    reader.releaseLock()
  }
}
