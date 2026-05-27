import { motion } from 'framer-motion'
import { Coins } from 'lucide-react'

import { useChatStore } from '../store/chatStore'

export function UsageBadge() {
  const messages = useChatStore((s) => s.activeMessages)

  const total = messages.reduce((sum, m) => sum + (m.tokens ?? 0), 0)
  if (total === 0) return null

  return (
    <motion.div
      initial={{ opacity: 0, scale: 0.9 }}
      animate={{ opacity: 1, scale: 1 }}
      transition={{ duration: 0.2 }}
      className="flex items-center gap-1.5 px-2.5 py-1 rounded-full
                 bg-white/[0.04] border border-white/10 text-[11px] text-white/60 font-mono"
    >
      <Coins size={11} className="text-amber-300/70" />
      {total.toLocaleString()} tok
    </motion.div>
  )
}
