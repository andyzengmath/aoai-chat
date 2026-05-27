import { useEffect } from 'react'
import clsx from 'clsx'

import { useChatStore } from '../store/chatStore'

export function Toast() {
  const toast = useChatStore((s) => s.toast)
  const setToast = useChatStore((s) => s.setToast)

  useEffect(() => {
    if (!toast) return
    const t = setTimeout(() => setToast(null), 5000)
    return () => clearTimeout(t)
  }, [toast, setToast])

  if (!toast) return null

  return (
    <div
      className={clsx(
        'fixed bottom-6 left-1/2 -translate-x-1/2 z-50',
        'px-4 py-2.5 rounded-xl text-sm font-medium shadow-2xl',
        'backdrop-blur-xl border',
        toast.kind === 'error' && 'bg-red-500/15 border-red-400/30 text-red-200',
        toast.kind === 'success' && 'bg-emerald-500/15 border-emerald-400/30 text-emerald-200',
        toast.kind === 'info' && 'bg-white/10 border-white/20 text-white',
      )}
    >
      {toast.message}
    </div>
  )
}
