import clsx from 'clsx'
import { motion } from 'framer-motion'
import { memo } from 'react'

import { MarkdownRenderer } from '../markdown/MarkdownRenderer'
import type { Message } from '../store/chatStore'

interface Props {
  msg: Message
  streaming?: boolean
}

function MessageBubbleImpl({ msg, streaming = false }: Props) {
  const isUser = msg.role === 'user'

  // Streaming bubbles use plain text to avoid re-parsing markdown per token.
  const renderAsPlainText = isUser || streaming

  return (
    <motion.div
      initial={streaming ? false : { opacity: 0, y: 6 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.18, ease: 'easeOut' }}
      className={clsx('flex gap-3', isUser ? 'justify-end' : 'justify-start')}
    >
      <div
        className={clsx(
          'min-w-0 max-w-[80ch] px-4 py-3 text-sm break-words glass',
          isUser
            ? 'glass-tint-user text-white'
            : 'glass-tint-assistant text-white/92',
        )}
      >
        {renderAsPlainText ? (
          <div className="whitespace-pre-wrap">
            {msg.content}
            {streaming && msg.content === '' && (
              <span className="text-white/40 italic">thinking…</span>
            )}
            {streaming && msg.content !== '' && (
              <span className="inline-block w-1.5 h-4 ml-0.5 -mb-0.5 bg-cyan-300 align-baseline animate-pulse" />
            )}
          </div>
        ) : (
          <div className="prose-aoai">
            <MarkdownRenderer content={msg.content} />
          </div>
        )}
        {!streaming && msg.role === 'assistant' && (msg.tokens || msg.deployment) && (
          <div className="mt-2 text-[10px] text-white/30 font-mono">
            {msg.deployment}
            {msg.tokens != null && ` · ${msg.tokens} tok`}
          </div>
        )}
      </div>
    </motion.div>
  )
}

export const MessageBubble = memo(
  MessageBubbleImpl,
  (prev, next) =>
    prev.msg.id === next.msg.id &&
    prev.msg.content === next.msg.content &&
    prev.streaming === next.streaming,
)
