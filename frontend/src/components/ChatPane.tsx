import { ArrowUpRight } from 'lucide-react'
import { useEffect, useRef } from 'react'
import { Virtuoso, type VirtuosoHandle } from 'react-virtuoso'

import { EXAMPLE_PROMPTS } from '../config/examples'
import type { Message } from '../store/chatStore'
import { useChatStore } from '../store/chatStore'

import { InlineError } from './InlineError'
import { MessageBubble } from './MessageBubble'
import { StreamingBubble } from './StreamingBubble'

export function ChatPane() {
  const messages = useChatStore((s) => s.activeMessages)
  const streaming = useChatStore((s) => s.streaming)
  const virtuosoRef = useRef<VirtuosoHandle | null>(null)

  // Original plan note: the streaming bubble lives **outside** the virtualized
  // list so its frequent re-renders (every token, every reasoning chunk)
  // never re-render the historical bubbles. Virtuoso's internal memoization
  // would otherwise skip propagating reasoning updates to the streaming row
  // because the row's `msg.content` doesn't change while reasoning streams.
  const items: Message[] = messages

  // Keep the bottom in view while streaming.
  const reasoningLen = streaming?.reasoning.length ?? 0
  useEffect(() => {
    if (streaming && virtuosoRef.current && messages.length > 0) {
      virtuosoRef.current.scrollToIndex({
        index: messages.length - 1,
        align: 'end',
        behavior: 'smooth',
      })
    }
  }, [messages.length, streaming?.content, reasoningLen, streaming])

  if (items.length === 0 && !streaming) {
    return <EmptyState />
  }

  return (
    <div className="relative flex-1 min-h-0 flex flex-col">
      {/* Reading lane — dims the cosmic chaos directly behind message text so
          long-form content stays readable. Fades out at the edges so the
          backdrop still bleeds in. */}
      <div
        className="pointer-events-none absolute inset-0 -z-0 reading-lane"
        aria-hidden
      />

      {items.length > 0 && (
        <Virtuoso
          ref={virtuosoRef}
          className="relative z-10"
          style={{ flex: 1, minHeight: 0 }}
          data={items}
          followOutput={false}
          computeItemKey={(_, msg) => msg.id}
          itemContent={(_, msg) => (
            <div className="px-6 py-2">
              <div className="max-w-3xl mx-auto">
                <MessageBubble msg={msg} />
              </div>
            </div>
          )}
          increaseViewportBy={{ top: 400, bottom: 600 }}
          initialTopMostItemIndex={Math.max(0, items.length - 1)}
        />
      )}
      {streaming && (
        <div className="relative z-10 shrink-0 px-6 py-2 max-h-[60vh] overflow-y-auto">
          <div className="max-w-3xl mx-auto">
            <StreamingBubble />
          </div>
        </div>
      )}
      <div className="relative z-10 px-6 empty:hidden">
        <div className="max-w-3xl mx-auto">
          <InlineError />
        </div>
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Empty state — modern, focused, Perplexity-flavored. Bold sans title,
// 4 clickable example prompts, no decorative illustration.
// ---------------------------------------------------------------------------

function EmptyState() {
  const sendMessage = useChatStore((s) => s.sendMessage)
  const selectedDeployment = useChatStore((s) => s.selectedDeployment)
  const params = useChatStore((s) => s.params)

  return (
    <div className="flex-1 flex items-center justify-center px-6 overflow-y-auto">
      <div className="w-full max-w-3xl py-12">
        <div className="text-center mb-12">
          <h2 className="text-[56px] font-medium tracking-[-0.04em] text-white leading-[1.0]">
            what would you like to{' '}
            <span className="text-sky-300">build</span>?
          </h2>
          <p className="mt-5 text-[12px] text-white/35 font-mono tracking-[0.06em]">
            <span className="text-emerald-400/80">●</span>&nbsp; {selectedDeployment || 'no deployment'}
            <span className="mx-2.5 text-white/15">·</span>
            {params.reasoningEffort}
            <span className="mx-2.5 text-white/15">·</span>
            {params.maxOutputTokens.toLocaleString()} tok
          </p>
        </div>

        <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
          {EXAMPLE_PROMPTS.map((ex) => (
            <button
              key={ex.category}
              onClick={() => void sendMessage(ex.prompt)}
              className="group glass px-4 py-3.5 text-left
                         hover:bg-white/[0.075] transition-all duration-150
                         focus:outline-none focus-visible:ring-1 focus-visible:ring-sky-300/40"
            >
              <div className="flex items-start justify-between gap-3">
                <div className="min-w-0">
                  <div className="text-[10px] uppercase tracking-[0.22em] text-sky-300/70 font-mono mb-1.5">
                    {ex.category}
                  </div>
                  <div className="text-[14px] text-white/90 leading-snug font-medium">
                    {ex.label}
                  </div>
                </div>
                <ArrowUpRight
                  size={14}
                  className="shrink-0 text-white/30 group-hover:text-sky-300/95 group-hover:-translate-y-0.5 group-hover:translate-x-0.5 transition-all mt-0.5"
                />
              </div>
            </button>
          ))}
        </div>

        <p className="mt-10 text-center text-[11px] text-white/25 font-mono tracking-[0.08em]">
          type below · <kbd>⌘K</kbd> switch chat · <kbd>⌘,</kbd> parameters
        </p>
      </div>
    </div>
  )
}
