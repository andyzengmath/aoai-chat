import clsx from 'clsx'
import { MessageSquarePlus, Settings as SettingsIcon, Trash2 } from 'lucide-react'

import { useChatStore } from '../store/chatStore'

export function Sidebar() {
  const conversations = useChatStore((s) => s.conversations)
  const activeId = useChatStore((s) => s.activeId)
  const conversationDeletionInFlight = useChatStore(
    (s) => s.conversationDeletionInFlight,
  )
  const selectConversation = useChatStore((s) => s.selectConversation)
  const newConversation = useChatStore((s) => s.newConversation)
  const deleteConversation = useChatStore((s) => s.deleteConversation)
  const openSettings = useChatStore((s) => s.openSettings)

  return (
    <aside className="relative z-10 w-72 shrink-0 border-r border-white/5 surface-chrome flex flex-col">
      <div className="p-3 border-b border-white/5">
        <button
          onClick={newConversation}
          className="w-full flex items-center justify-center gap-2 px-3 py-2.5 rounded-xl
                     bg-white/5 hover:bg-white/10 border border-white/10
                     text-sm font-medium text-white transition-colors"
        >
          <MessageSquarePlus size={16} />
          New chat
        </button>
      </div>

      <div className="flex-1 overflow-y-auto px-2 py-2 space-y-1">
        {conversations.length === 0 && (
          <div className="text-center text-xs text-white/30 py-8 px-3">
            No conversations yet. Send a message to start one.
          </div>
        )}
        {conversations.map((c) => (
          <div
            key={c.id}
            onClick={() => selectConversation(c.id)}
            className={clsx(
              'group cursor-pointer px-3 py-2 rounded-lg text-sm',
              'flex items-center justify-between gap-2',
              'transition-colors',
              activeId === c.id ? 'bg-white/10 text-white' : 'text-white/60 hover:bg-white/5 hover:text-white/90',
            )}
          >
            <div className="min-w-0 flex-1">
              <div className="truncate">{c.title || c.filename}</div>
              <div className="text-[10px] text-white/30 mt-0.5">
                {c.turn_count} turn{c.turn_count === 1 ? '' : 's'} · {c.deployment || '?'}
              </div>
            </div>
            <button
              onClick={(e) => {
                e.stopPropagation()
                if (confirm(`Delete "${c.title}"?`)) deleteConversation(c.id)
              }}
              disabled={conversationDeletionInFlight}
              className="opacity-0 group-hover:opacity-100 text-white/40
                         hover:text-red-400 transition-opacity
                         disabled:cursor-not-allowed disabled:hover:text-white/40"
              title="Delete"
            >
              <Trash2 size={14} />
            </button>
          </div>
        ))}
      </div>

      <div className="p-3 border-t border-white/5">
        <button
          onClick={() => openSettings(true)}
          className="w-full flex items-center gap-2 px-3 py-2 rounded-lg
                     text-white/60 hover:text-white hover:bg-white/5
                     text-sm transition-colors"
        >
          <SettingsIcon size={16} />
          Settings
        </button>
      </div>
    </aside>
  )
}
