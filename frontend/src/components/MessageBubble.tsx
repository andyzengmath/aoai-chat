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
        {!streaming && msg.role === 'assistant' && (
          <MessageFooter msg={msg} />
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

function formatThinking(ms: number): string {
  const s = Math.round(ms / 1000)
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

function MessageFooter({ msg }: { msg: Message }) {
  const parts: string[] = []
  if (msg.deployment) parts.push(msg.deployment)
  if (msg.thinkingMs != null && msg.thinkingMs > 0) {
    parts.push(`thought ${formatThinking(msg.thinkingMs)}`)
  }
  if (msg.reasoningTokens != null && msg.reasoningTokens > 0) {
    parts.push(`${msg.reasoningTokens.toLocaleString()} reasoning tok`)
  }
  if (msg.reasoningChars != null && msg.reasoningChars > 0) {
    parts.push(`${msg.reasoningChars.toLocaleString()} ch reasoning`)
  }
  if (msg.tokens != null) parts.push(`${msg.tokens.toLocaleString()} tok`)
  if (msg.path) parts.push(msg.path === 'responses' ? 'responses · v1' : 'chat completions')
  if (parts.length === 0) return null
  return (
    <div className="mt-2 text-[10px] text-white/30 font-mono">
      {parts.join(' · ')}
    </div>
  )
}
