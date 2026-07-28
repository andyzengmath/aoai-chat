import { AnimatePresence, motion } from 'framer-motion'
import { Brain, ChevronDown, ChevronRight, Clock, RotateCw, Square } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'

import { PONDERING_PHRASES, pickPondering } from '../config/thinking'
import { useChatStore } from '../store/chatStore'

function formatElapsed(ms: number): string {
  const s = Math.floor(ms / 1000)
  if (s < 60) return `${s}s`
  if (s < 3600) {
    const m = Math.floor(s / 60)
    const rem = s % 60
    return `${m}m ${rem.toString().padStart(2, '0')}s`
  }
  const h = Math.floor(s / 3600)
  const m = Math.floor((s % 3600) / 60)
  return `${h}h ${m.toString().padStart(2, '0')}m`
}

function ElapsedTimer({ startedAt }: { startedAt: number }) {
  const [, force] = useState(0)
  useEffect(() => {
    const id = setInterval(() => force((x) => x + 1), 1000)
    return () => clearInterval(id)
  }, [])
  const elapsed = Date.now() - startedAt
  return (
    <span className="inline-flex items-center gap-1 text-[11px] font-mono text-cyan-200/65">
      <Clock size={10} />
      {formatElapsed(elapsed)}
    </span>
  )
}

/**
 * "Last update Xs ago" indicator. Green pulse when fresh (<5s), white when
 * recent (5–30s), amber after 30s, rose after 90s. Lets the user tell
 * "model is in a quiet reasoning phase" from "stream is truly stuck"
 * without needing to wait or read network logs.
 */
function FreshnessDot({ lastEventAt }: { lastEventAt: number }) {
  const [, force] = useState(0)
  useEffect(() => {
    const id = setInterval(() => force((x) => x + 1), 1000)
    return () => clearInterval(id)
  }, [])
  const sinceMs = Date.now() - lastEventAt
  const since = Math.floor(sinceMs / 1000)

  let label: string
  if (since < 2) label = 'live'
  else if (since < 60) label = `${since}s ago`
  else if (since < 3600) label = `${Math.floor(since / 60)}m ${since % 60}s ago`
  else label = `${Math.floor(since / 3600)}h ${Math.floor((since % 3600) / 60)}m ago`

  let dotColor: string
  let textColor: string
  if (sinceMs < 5_000) {
    dotColor = 'bg-emerald-400 shadow-[0_0_6px_rgba(74,222,128,0.7)]'
    textColor = 'text-emerald-300/75'
  } else if (sinceMs < 30_000) {
    dotColor = 'bg-cyan-300/80'
    textColor = 'text-cyan-200/55'
  } else if (sinceMs < 90_000) {
    dotColor = 'bg-amber-300/90'
    textColor = 'text-amber-200/75'
  } else {
    dotColor = 'bg-rose-400'
    textColor = 'text-rose-300/80'
  }

  return (
    <span
      className={`inline-flex items-center gap-1.5 text-[10px] font-mono ${textColor}`}
      title="Time since the last stream event arrived"
    >
      <span
        className={`inline-block w-1.5 h-1.5 rounded-full ${dotColor} ${
          sinceMs < 5_000 ? 'animate-pulse' : ''
        }`}
      />
      {label}
    </span>
  )
}

function redshiftClass(elapsedMs: number): string {
  // Cool to warm as time passes — like radiation from an infalling photon.
  if (elapsedMs < 10_000) return 'pondering-redshift-0'
  if (elapsedMs < 25_000) return 'pondering-redshift-1'
  if (elapsedMs < 60_000) return 'pondering-redshift-2'
  return 'pondering-redshift-3'
}

function PonderingFlavor({ startedAt }: { startedAt: number }) {
  const [phrase, setPhrase] = useState(() => pickPondering())
  const [, force] = useState(0)
  useEffect(() => {
    // Rotate every 60s — long enough to read each phrase, slow enough to
    // not feel restless. The first phrase is picked at mount, so quick
    // pondering windows (< 60s) just show one phrase the whole way through.
    const rotate = setInterval(() => setPhrase((p) => pickPondering(p)), 60_000)
    // Re-tick the redshift class once per 5s so the color advance feels live.
    const tick = setInterval(() => force((x) => x + 1), 5000)
    return () => {
      clearInterval(rotate)
      clearInterval(tick)
    }
  }, [])
  const shiftClass = redshiftClass(Date.now() - startedAt)
  return (
    <div
      className={`relative min-h-[1.6em] flex items-center font-display italic text-[15px] tracking-tight ${shiftClass}`}
    >
      <AnimatePresence mode="wait">
        <motion.span
          key={phrase}
          initial={{ opacity: 0, y: 4, filter: 'blur(3px)' }}
          animate={{ opacity: 1, y: 0, filter: 'blur(0)' }}
          exit={{ opacity: 0, y: -4, filter: 'blur(3px)' }}
          transition={{ duration: 0.42, ease: 'easeOut' }}
          className="inline-flex items-baseline gap-1"
        >
          {phrase}
          <span className="inline-block w-1 h-3 ml-1 bg-current opacity-70 animate-pulse" />
        </motion.span>
      </AnimatePresence>
    </div>
  )
}

export function StreamingBubble() {
  const streaming = useChatStore((s) => s.streaming)
  const stopStream = useChatStore((s) => s.stopStream)
  const [reasoningOpen, setReasoningOpen] = useState(true)

  if (!streaming) return null

  const { content, reasoning, startedAt } = streaming
  const showPondering = content === '' && reasoning === ''

  return (
    <div className="flex gap-3 justify-start">
      <div className="min-w-0 max-w-[80ch] px-4 py-3 text-sm break-words glass glass-tint-assistant text-white/92">
        {/* Timer + freshness indicator + stop button + path badge */}
        <div className="flex items-center justify-between gap-2 mb-2 -mt-0.5 flex-wrap">
          <div className="flex items-center gap-2.5">
            <ElapsedTimer startedAt={startedAt} />
            <FreshnessDot lastEventAt={streaming.lastEventAt} />
            <button
              onClick={stopStream}
              title="Stop generation (partial output discarded)"
              className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded-md
                         text-[10px] font-mono uppercase tracking-[0.14em]
                         text-rose-300/70 hover:text-rose-200
                         bg-rose-500/[0.06] hover:bg-rose-500/[0.12]
                         border border-rose-400/20 hover:border-rose-400/35
                         transition-colors"
            >
              <Square size={9} className="fill-current" />
              stop
            </button>
          </div>
          {streaming.path && (
            <span className="text-[10px] font-mono text-white/30 uppercase tracking-[0.18em]">
              {streaming.path === 'responses' ? 'responses · v1' : 'chat completions'}
            </span>
          )}
        </div>

        {streaming.retrying && (
          <div
            className="mb-3 inline-flex items-center gap-1.5 rounded-md
                       border border-amber-300/20 bg-amber-400/[0.07]
                       px-2.5 py-1.5 text-[11px] font-mono text-amber-100/75"
          >
            <RotateCw size={11} className="animate-spin" />
            Retrying {streaming.retrying.attempt}/{streaming.retrying.maxAttempts} in{' '}
            {streaming.retrying.delaySeconds}s
          </div>
        )}

        {/* Reasoning summary — ChatGPT-style: only the latest ~5 lines are
            visible, with the top edge fading out. The full reasoning lives in
            store state (and ends up in the transcript file) but we don't dump
            35k chars of internal monologue into the chat. The header still
            shows the total char count so it's clear there's more under the
            surface. */}
        {reasoning && (
          <div className="mb-3 rounded-lg border border-violet-300/15 bg-violet-500/[0.04]">
            <button
              onClick={() => setReasoningOpen((o) => !o)}
              className="w-full flex items-center gap-1.5 px-3 py-1.5 text-[11px]
                         text-white/50 hover:text-white/80 transition-colors"
            >
              {reasoningOpen ? <ChevronDown size={11} /> : <ChevronRight size={11} />}
              <Brain size={11} className="text-violet-300/80" />
              <span className="uppercase tracking-[0.18em] font-medium">Thinking</span>
              <span className="text-white/30">·</span>
              <span className="font-mono text-white/30">
                {streaming.reasoningCharsTotal.toLocaleString()} ch
              </span>
            </button>
            {reasoningOpen && (
              <CompactReasoning text={reasoning} />
            )}
          </div>
        )}

        {/* Main streamed content */}
        {showPondering ? (
          <PonderingFlavor startedAt={startedAt} />
        ) : (
          <div className="whitespace-pre-wrap">
            {content}
            {content !== '' && (
              <span className="inline-block w-1.5 h-4 ml-0.5 -mb-0.5 bg-cyan-300 align-baseline animate-pulse" />
            )}
          </div>
        )}
      </div>
    </div>
  )
}

// Keep the import alive for tree-shaking visibility (helps dev tooling).
void PONDERING_PHRASES

// ----------------------------------------------------------------------------
// Reasoning ticker
// ----------------------------------------------------------------------------
// Shows only the last ~500 chars of the model's reasoning stream, with the
// top edge fading out so older lines visibly "scroll off". The container is
// height-capped to roughly 5 lines. Auto-scrolls to the bottom on each
// update so the freshest token is always pinned at the visible baseline,
// matching the ChatGPT live-reasoning ticker.
// ----------------------------------------------------------------------------

const COMPACT_REASONING_TAIL_CHARS = 500

function CompactReasoning({ text }: { text: string }) {
  const boxRef = useRef<HTMLDivElement | null>(null)
  const tail = text.length > COMPACT_REASONING_TAIL_CHARS
    ? '… ' + text.slice(text.length - COMPACT_REASONING_TAIL_CHARS)
    : text

  useEffect(() => {
    const el = boxRef.current
    if (el) el.scrollTop = el.scrollHeight
  }, [text])

  return (
    <div
      ref={boxRef}
      className="px-3 pb-3 pt-2 text-[12.5px] leading-relaxed text-violet-100/70
                 font-display italic whitespace-pre-wrap break-words"
      style={{
        maxHeight: '6.5em',
        overflow: 'hidden',
        maskImage:
          'linear-gradient(180deg, transparent 0%, black 28%, black 100%)',
        WebkitMaskImage:
          'linear-gradient(180deg, transparent 0%, black 28%, black 100%)',
      }}
    >
      {tail}
      <span className="inline-block w-1 h-3 ml-0.5 -mb-0.5 bg-violet-300/60 align-baseline animate-pulse" />
    </div>
  )
}
