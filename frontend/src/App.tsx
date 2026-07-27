import { SlidersHorizontal } from 'lucide-react'
import { useEffect, useState } from 'react'

import { ChatPane } from './components/ChatPane'
import { CommandPalette } from './components/CommandPalette'
import { InputBar } from './components/InputBar'
import { ModelPicker } from './components/ModelPicker'
import { ParametersPanel } from './components/ParametersPanel'
import { SettingsDialog } from './components/SettingsDialog'
import { Sidebar } from './components/Sidebar'
import { Telemetry } from './components/Telemetry'
import { Toast } from './components/Toast'
import { UsageBadge } from './components/UsageBadge'
import { PROMPT_PRESETS } from './config/presets'
import { useChatStore } from './store/chatStore'

export default function App() {
  const bootstrap = useChatStore((s) => s.bootstrap)
  const openParameters = useChatStore((s) => s.openParameters)
  const params = useChatStore((s) => s.params)
  const [paletteOpen, setPaletteOpen] = useState(false)

  const activePresetLabel =
    PROMPT_PRESETS.find((p) => p.prompt === params.systemPrompt)?.label ?? 'Custom'

  useEffect(() => {
    void bootstrap()
  }, [bootstrap])

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') {
        e.preventDefault()
        setPaletteOpen((v) => !v)
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [])

  return (
    <div className="h-screen w-screen flex text-white overflow-hidden cosmos-bg">
      {/* Static cosmic decor. All animation removed — the gradients in
          `.cosmos-bg` (on body) already create ambient color depth, and
          static stars look like stars without the per-frame repaint cost.
          Result: zero continuous CPU/GPU work for the backdrop. */}
      <div className="starfield" />
      <div className="distant-galaxy" />
      <div className="lead-stars">
        <span />
        <span />
      </div>

      <Sidebar />

      <main className="relative z-10 flex-1 flex flex-col min-w-0">
        <header className="relative border-b border-white/5 surface-chrome">
          {/* Glowing baseline scanner */}
          <div className="absolute bottom-0 left-0 right-0 h-px bg-gradient-to-r from-transparent via-cyan-400/35 to-transparent" />
          <div className="px-6 py-3 flex items-center justify-between gap-3">
            <div className="flex items-center gap-4">
              <ModelPicker />
              <Telemetry />
            </div>
            <div className="flex items-center gap-2">
              <UsageBadge />
              <button
                onClick={() => openParameters(true)}
                className="flex items-center gap-1.5 px-2.5 py-1 rounded-lg
                           bg-white/[0.04] hover:bg-white/[0.08] border border-white/10
                           text-[11px] text-white/60 hover:text-white transition-colors"
                title="Adjust system prompt, reasoning effort, and max tokens"
              >
                <SlidersHorizontal size={12} />
                <span className="hidden sm:inline">{activePresetLabel}</span>
                <span className="hidden sm:inline text-white/30">·</span>
                <span className="font-mono">
                  {params.reasoningMode === 'pro' ? 'pro · ' : ''}
                  {params.reasoningEffort}
                </span>
              </button>
              <button
                onClick={() => setPaletteOpen(true)}
                className="hidden sm:flex items-center gap-1.5 px-2.5 py-1 rounded-lg
                           bg-white/[0.04] hover:bg-white/[0.08] border border-white/10
                           text-[11px] text-white/40 hover:text-white/70 font-mono transition-colors"
                title="Open command palette"
              >
                <kbd>⌘</kbd>
                <kbd>K</kbd>
              </button>
            </div>
          </div>
        </header>

        <ChatPane />
        <InputBar />
      </main>

      <SettingsDialog />
      <ParametersPanel />
      <CommandPalette open={paletteOpen} onOpenChange={setPaletteOpen} />
      <Toast />
    </div>
  )
}
