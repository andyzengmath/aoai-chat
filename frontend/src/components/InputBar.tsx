import { Send } from 'lucide-react'
import { useRef, useState, type KeyboardEvent } from 'react'

import { useChatStore } from '../store/chatStore'

/** True iff the caret sits on the textarea's first visual line. */
function caretAtFirstLine(el: HTMLTextAreaElement): boolean {
  const pos = el.selectionStart ?? 0
  return el.value.lastIndexOf('\n', pos - 1) === -1
}

/** True iff the caret sits on the textarea's last visual line. */
function caretAtLastLine(el: HTMLTextAreaElement): boolean {
  const pos = el.selectionEnd ?? 0
  return el.value.indexOf('\n', pos) === -1
}

export function InputBar() {
  const [text, setText] = useState('')
  const sendMessage = useChatStore((s) => s.sendMessage)
  const streaming = useChatStore((s) => s.streaming)
  const conversationLoadingId = useChatStore((s) => s.conversationLoadingId)
  const selectedDeployment = useChatStore((s) => s.selectedDeployment)
  const promptHistory = useChatStore((s) => s.promptHistory)
  const taRef = useRef<HTMLTextAreaElement | null>(null)

  // Terminal-style history navigation.
  // - `historyIndex === null`  → not browsing (showing the user's live draft)
  // - `historyIndex >= 0`      → showing `promptHistory[historyIndex]`
  // - `draftRef.current`       → the draft that was in the box when the user
  //                              first pressed Up; restored when they Down
  //                              past the newest history entry.
  const [historyIndex, setHistoryIndex] = useState<number | null>(null)
  const draftRef = useRef('')

  const canSend =
    !streaming
    && !conversationLoadingId
    && text.trim().length > 0
    && !!selectedDeployment

  const setTextAndPlaceCaretAtEnd = (next: string) => {
    setText(next)
    requestAnimationFrame(() => {
      const el = taRef.current
      if (el) {
        el.selectionStart = el.selectionEnd = el.value.length
        el.scrollTop = el.scrollHeight
      }
    })
  }

  const submit = () => {
    if (!canSend) return
    const v = text
    setText('')
    setHistoryIndex(null)
    draftRef.current = ''
    void sendMessage(v)
    requestAnimationFrame(() => taRef.current?.focus())
  }

  const onKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      submit()
      return
    }

    if (e.key === 'ArrowUp') {
      const el = taRef.current
      if (!el) return
      if (!caretAtFirstLine(el)) return // let default cursor-up handle within multi-line
      if (promptHistory.length === 0) return
      e.preventDefault()

      if (historyIndex === null) {
        // Entering history: snapshot the current draft so Down can restore it.
        draftRef.current = text
        const newIdx = promptHistory.length - 1
        setHistoryIndex(newIdx)
        setTextAndPlaceCaretAtEnd(promptHistory[newIdx])
      } else if (historyIndex > 0) {
        const newIdx = historyIndex - 1
        setHistoryIndex(newIdx)
        setTextAndPlaceCaretAtEnd(promptHistory[newIdx])
      }
      return
    }

    if (e.key === 'ArrowDown') {
      if (historyIndex === null) return // not browsing — let default cursor-down behave
      const el = taRef.current
      if (el && !caretAtLastLine(el)) return // multi-line cursor movement inside history entry
      e.preventDefault()

      if (historyIndex < promptHistory.length - 1) {
        const newIdx = historyIndex + 1
        setHistoryIndex(newIdx)
        setTextAndPlaceCaretAtEnd(promptHistory[newIdx])
      } else {
        // Past the newest — restore the draft (or empty).
        setHistoryIndex(null)
        setTextAndPlaceCaretAtEnd(draftRef.current)
        draftRef.current = ''
      }
      return
    }
  }

  const browsing = historyIndex !== null
  const browsingPosition = browsing
    ? `${historyIndex + 1} / ${promptHistory.length}`
    : null

  return (
    <div className="border-t border-white/5 bg-black/40">
      <div className="max-w-3xl mx-auto px-6 py-4">
        <div className="relative flex items-end gap-2 glass-input focus-within:border-sky-300/40 transition-colors">
          <textarea
            ref={taRef}
            value={text}
            onChange={(e) => {
              setText(e.target.value)
              // Manual edits exit history navigation — the box now represents
              // the user's own draft, not a recalled entry.
              if (historyIndex !== null) {
                setHistoryIndex(null)
                draftRef.current = ''
              }
            }}
            onKeyDown={onKeyDown}
            disabled={!!conversationLoadingId}
            placeholder={
              conversationLoadingId
                ? 'Loading conversation…'
                : selectedDeployment
                ? `Message ${selectedDeployment}…  (↑↓ for history · Shift+Enter for newline)`
                : 'Pick a deployment in Settings first'
            }
            rows={1}
            className="flex-1 bg-transparent resize-none px-4 py-3 text-sm
                       text-white placeholder:text-white/30 focus:outline-none
                       max-h-48 min-h-[2.75rem]"
            style={{ scrollbarWidth: 'thin' }}
          />
          <button
            onClick={submit}
            disabled={!canSend}
            className="m-1.5 p-2 rounded-xl bg-cyan-400/20 hover:bg-cyan-400/30
                       border border-cyan-300/30
                       disabled:opacity-30 disabled:cursor-not-allowed disabled:hover:bg-cyan-400/20
                       transition-colors text-cyan-300"
            title="Send (Enter)"
          >
            <Send size={16} />
          </button>
          {browsingPosition && (
            <span
              className="pointer-events-none absolute -top-5 right-3 text-[10px] font-mono text-cyan-300/55 tracking-wider"
              aria-hidden
            >
              history · {browsingPosition}
            </span>
          )}
        </div>
      </div>
    </div>
  )
}
