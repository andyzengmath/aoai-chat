import * as Dialog from '@radix-ui/react-dialog'
import { AnimatePresence, motion } from 'framer-motion'
import { MessageSquare, Plus, Search, Settings } from 'lucide-react'
import { useEffect, useMemo, useRef, useState, type KeyboardEvent } from 'react'

import { useChatStore } from '../store/chatStore'

interface PaletteItem {
  id: string
  label: string
  hint?: string
  icon: typeof Search
  run: () => void
}

interface Props {
  open: boolean
  onOpenChange: (open: boolean) => void
}

export function CommandPalette({ open, onOpenChange }: Props) {
  const conversations = useChatStore((s) => s.conversations)
  const selectConversation = useChatStore((s) => s.selectConversation)
  const newConversation = useChatStore((s) => s.newConversation)
  const openSettings = useChatStore((s) => s.openSettings)

  const [query, setQuery] = useState('')
  const [cursor, setCursor] = useState(0)
  const inputRef = useRef<HTMLInputElement | null>(null)

  useEffect(() => {
    if (open) {
      setQuery('')
      setCursor(0)
      requestAnimationFrame(() => inputRef.current?.focus())
    }
  }, [open])

  const items = useMemo<PaletteItem[]>(() => {
    const base: PaletteItem[] = [
      {
        id: 'new',
        label: 'New chat',
        icon: Plus,
        run: () => {
          newConversation()
          onOpenChange(false)
        },
      },
      {
        id: 'settings',
        label: 'Open Settings',
        icon: Settings,
        run: () => {
          openSettings(true)
          onOpenChange(false)
        },
      },
    ]
    for (const c of conversations) {
      base.push({
        id: c.id,
        label: c.title || c.filename,
        hint: `${c.deployment} · ${c.turn_count} turn${c.turn_count === 1 ? '' : 's'}`,
        icon: MessageSquare,
        run: () => {
          void selectConversation(c.id)
          onOpenChange(false)
        },
      })
    }
    const q = query.trim().toLowerCase()
    return q
      ? base.filter((it) => it.label.toLowerCase().includes(q) || (it.hint || '').toLowerCase().includes(q))
      : base
  }, [conversations, newConversation, onOpenChange, openSettings, query, selectConversation])

  const onKeyDown = (e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'ArrowDown') {
      e.preventDefault()
      setCursor((c) => Math.min(items.length - 1, c + 1))
    } else if (e.key === 'ArrowUp') {
      e.preventDefault()
      setCursor((c) => Math.max(0, c - 1))
    } else if (e.key === 'Enter') {
      e.preventDefault()
      items[cursor]?.run()
    }
  }

  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <AnimatePresence>
        {open && (
          <Dialog.Portal forceMount>
            <Dialog.Overlay asChild>
              <motion.div
                initial={{ opacity: 0 }}
                animate={{ opacity: 1 }}
                exit={{ opacity: 0 }}
                transition={{ duration: 0.15 }}
                className="fixed inset-0 z-40 surface-overlay"
              />
            </Dialog.Overlay>

            <Dialog.Content asChild>
              <motion.div
                initial={{ opacity: 0, y: -8, scale: 0.98 }}
                animate={{ opacity: 1, y: 0, scale: 1 }}
                exit={{ opacity: 0, y: -4, scale: 0.98 }}
                transition={{ duration: 0.18, ease: 'easeOut' }}
                className="fixed left-1/2 top-[15%] -translate-x-1/2 z-50
                           w-[min(600px,calc(100vw-2rem))] max-h-[60vh] overflow-hidden
                           rounded-2xl border border-white/10 surface-elevated
                           flex flex-col"
              >
                <Dialog.Title className="sr-only">Command palette</Dialog.Title>
                <div className="flex items-center gap-2 px-4 py-3 border-b border-white/5">
                  <Search size={16} className="text-white/40" />
                  <input
                    ref={inputRef}
                    value={query}
                    onChange={(e) => {
                      setQuery(e.target.value)
                      setCursor(0)
                    }}
                    onKeyDown={onKeyDown}
                    placeholder="Search conversations…"
                    className="flex-1 bg-transparent text-sm text-white placeholder:text-white/30 focus:outline-none"
                  />
                  <kbd className="text-[10px] text-white/30 font-mono">esc</kbd>
                </div>

                <div className="flex-1 overflow-y-auto p-1.5">
                  {items.length === 0 && (
                    <div className="text-center text-xs text-white/30 py-6">No matches</div>
                  )}
                  {items.map((it, i) => (
                    <button
                      key={it.id}
                      onMouseEnter={() => setCursor(i)}
                      onClick={() => it.run()}
                      className={`w-full text-left flex items-center gap-3 px-3 py-2 rounded-lg
                                  text-sm transition-colors
                                  ${i === cursor ? 'bg-white/10 text-white' : 'text-white/70'}`}
                    >
                      <it.icon size={14} className={i === cursor ? 'text-cyan-300' : 'text-white/40'} />
                      <div className="flex-1 min-w-0">
                        <div className="truncate">{it.label}</div>
                        {it.hint && (
                          <div className="text-[10px] text-white/30 font-mono truncate">{it.hint}</div>
                        )}
                      </div>
                      {i === cursor && (
                        <kbd className="text-[10px] text-white/30 font-mono">↵</kbd>
                      )}
                    </button>
                  ))}
                </div>

                <div className="px-4 py-2 border-t border-white/5 text-[10px] text-white/30 font-mono flex justify-between">
                  <span>↑↓ navigate · ↵ select</span>
                  <span>⌘K to open</span>
                </div>
              </motion.div>
            </Dialog.Content>
          </Dialog.Portal>
        )}
      </AnimatePresence>
    </Dialog.Root>
  )
}
