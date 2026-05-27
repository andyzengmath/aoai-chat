import { useEffect, useState } from 'react'

/**
 * Decorative header readout — ticks every second. Pure ornament; doesn't
 * affect any chat behavior. Reinforces the "research vessel console" feel.
 */
function pad(n: number, width = 2): string {
  return n.toString().padStart(width, '0')
}

function utcStamp(d: Date): string {
  return `${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())}:${pad(d.getUTCSeconds())}`
}

const SECTOR_LABELS = [
  'SEC EOS-7',
  'SEC LYR-3',
  'SEC ORI-12',
  'SEC CYG-X1',
  'SEC SGR-A*',
] as const

export function Telemetry() {
  const [now, setNow] = useState(() => new Date())
  // Sector is randomized once per mount to give a little variety per session.
  const [sector] = useState(
    () => SECTOR_LABELS[Math.floor(Math.random() * SECTOR_LABELS.length)],
  )

  useEffect(() => {
    const id = setInterval(() => setNow(new Date()), 1000)
    return () => clearInterval(id)
  }, [])

  return (
    <div className="hidden md:flex items-center gap-2 text-[10px] font-mono uppercase tracking-[0.22em] text-white/30">
      <span className="block h-px w-5 bg-cyan-400/35" />
      <span className="flex items-center gap-1.5">
        <span className="inline-block w-1 h-1 rounded-full bg-emerald-400/80 shadow-[0_0_6px_rgba(74,222,128,0.7)] animate-pulse" />
        <span>{sector}</span>
      </span>
      <span className="text-white/15">·</span>
      <span className="text-cyan-300/55">{utcStamp(now)} UTC</span>
      <span className="block h-px w-5 bg-cyan-400/35" />
    </div>
  )
}
