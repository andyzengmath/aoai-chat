import { AlertTriangle, Play, RotateCcw, X } from 'lucide-react'

import { useChatStore } from '../store/chatStore'

/**
 * Persistent inline error card. Shown below the conversation when the last
 * turn failed or returned an empty response. Survives until the user retries
 * or dismisses it, so they can't miss it the way they'd miss a toast.
 */
export function InlineError() {
  const lastError = useChatStore((s) => s.lastError)
  const streaming = useChatStore((s) => s.streaming)
  const retryLastError = useChatStore((s) => s.retryLastError)
  const continueLastIncomplete = useChatStore((s) => s.continueLastIncomplete)
  const clearLastError = useChatStore((s) => s.clearLastError)

  if (!lastError || streaming) return null

  return (
    <div className="flex gap-3 justify-start">
      <div
        className="min-w-0 max-w-[80ch] flex-1 rounded-xl border border-rose-400/30 bg-rose-500/[0.06]
                   text-rose-100/90"
      >
        <div className="flex items-start gap-2.5 px-4 py-3">
          <AlertTriangle
            size={14}
            className="shrink-0 mt-0.5 text-rose-300"
          />
          <div className="min-w-0 flex-1">
            <div className="text-[10px] uppercase tracking-[0.2em] font-mono text-rose-300/80">
              {lastError.kind === 'incomplete' ? 'Response incomplete' : 'No response'}
            </div>
            <p className="mt-1 text-[13px] leading-relaxed text-rose-100/85">
              {lastError.message}
            </p>
            <div className="mt-3 flex items-center gap-2">
              {lastError.kind === 'incomplete' ? (
                <button
                  onClick={continueLastIncomplete}
                  className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg
                             bg-rose-400/15 hover:bg-rose-400/25
                             border border-rose-300/30
                             text-[12px] text-rose-100 font-medium
                             transition-colors"
                >
                  <Play size={11} />
                  Continue
                </button>
              ) : (
                <button
                  onClick={retryLastError}
                  className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg
                             bg-rose-400/15 hover:bg-rose-400/25
                             border border-rose-300/30
                             text-[12px] text-rose-100 font-medium
                             transition-colors"
                >
                  <RotateCcw size={11} />
                  Retry
                </button>
              )}
              <button
                onClick={clearLastError}
                className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg
                           hover:bg-white/5
                           text-[12px] text-white/50 hover:text-white/80
                           transition-colors"
              >
                <X size={11} />
                Dismiss
              </button>
              <span className="ml-auto text-[10px] font-mono text-white/30">
                {lastError.kind === 'incomplete'
                  ? 'Continues from the saved Azure response'
                  : 'Up-arrow also recalls this prompt'}
              </span>
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}
