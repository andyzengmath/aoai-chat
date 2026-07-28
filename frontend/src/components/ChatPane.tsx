import { ArrowUpRight } from 'lucide-react'
import { useEffect, useLayoutEffect, useRef } from 'react'

import { EXAMPLE_PROMPTS } from '../config/examples'
import type { Message } from '../store/chatStore'
import { useChatStore } from '../store/chatStore'

import { InlineError } from './InlineError'
import { MessageBubble } from './MessageBubble'
import { StreamingBubble } from './StreamingBubble'

export function ChatPane() {
  const messages = useChatStore((s) => s.activeMessages)
  const activeId = useChatStore((s) => s.activeId)
  const streaming = useChatStore((s) => s.streaming)
  const scrollerRef = useRef<HTMLDivElement | null>(null)
  const contentRef = useRef<HTMLDivElement | null>(null)
  const pinnedToBottomRef = useRef(true)
  const previousConversationIdRef = useRef(activeId)
  const lastScrollTopRef = useRef(0)

  const items: Message[] = messages

  useLayoutEffect(() => {
    const conversationChanged =
      previousConversationIdRef.current !== activeId
    previousConversationIdRef.current = activeId
    const scroller = scrollerRef.current
    if (!scroller || messages.length === 0) return
    const lastMessage = messages[messages.length - 1]
    if (
      !pinnedToBottomRef.current
      && !conversationChanged
      && lastMessage.role !== 'user'
    ) {
      return
    }
    pinnedToBottomRef.current = true
    scroller.scrollTop = scroller.scrollHeight
    lastScrollTopRef.current = scroller.scrollTop
  }, [activeId, messages])

  // Late font/layout changes can resize long Markdown messages after render.
  // Stay pinned only while the user remains at the end; scrolling upward
  // immediately hands full control back to the user.
  useEffect(() => {
    const scroller = scrollerRef.current
    const content = contentRef.current
    if (!scroller || !content) return
    const observer = new ResizeObserver(() => {
      if (pinnedToBottomRef.current) {
        scroller.scrollTop = scroller.scrollHeight
        lastScrollTopRef.current = scroller.scrollTop
      }
    })
    observer.observe(content)
    observer.observe(scroller)
    return () => observer.disconnect()
  }, [messages.length])

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
        <div
          ref={scrollerRef}
          data-testid="conversation-scroller"
          className="relative z-10 flex-1 min-h-0 overflow-y-auto"
          onScroll={(event) => {
            const scroller = event.currentTarget
            const movedUp =
              scroller.scrollTop < lastScrollTopRef.current - 0.5
            const atBottom =
              scroller.scrollHeight
              - scroller.clientHeight
              - scroller.scrollTop <= 1
            if (movedUp) {
              pinnedToBottomRef.current = false
            } else if (atBottom) {
              pinnedToBottomRef.current = true
            }
            lastScrollTopRef.current = scroller.scrollTop
          }}
        >
          <div ref={contentRef}>
            {items.map((msg) => (
              <div
                key={msg.id}
                className={`conversation-message px-6 py-2 ${
                  msg.role === 'assistant'
                    ? 'conversation-message-assistant'
                    : 'conversation-message-user'
                }`}
              >
                <div className="max-w-3xl mx-auto">
                  <MessageBubble msg={msg} />
                </div>
              </div>
            ))}
          </div>
        </div>
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
            {params.reasoningMode === 'pro' ? 'pro · ' : ''}
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
