import { AnimatePresence, motion } from 'framer-motion'
import { RotateCcw, Sparkles, X } from 'lucide-react'
import { useEffect, useMemo } from 'react'

import {
  getValidEfforts,
  MAX_TOKENS_BOUNDS,
  PROMPT_PRESETS,
  type ReasoningEffort,
} from '../config/presets'
import { useChatStore } from '../store/chatStore'

export function ParametersPanel() {
  const open = useChatStore((s) => s.parametersOpen)
  const close = () => useChatStore.getState().openParameters(false)
  const params = useChatStore((s) => s.params)
  const setParams = useChatStore((s) => s.setParams)
  const resetParams = useChatStore((s) => s.resetParams)
  const deployments = useChatStore((s) => s.deployments)
  const selectedDeployment = useChatStore((s) => s.selectedDeployment)

  // Reasoning effort only applies to Responses-API-capable deployments.
  const showReasoning = useMemo(
    () => deployments.find((d) => d.id === selectedDeployment)?.supports_responses_api ?? false,
    [deployments, selectedDeployment],
  )

  const validEfforts = useMemo(
    () => getValidEfforts(selectedDeployment),
    [selectedDeployment],
  )

  // If the current effort isn't valid for the selected model, coerce it.
  useEffect(() => {
    if (showReasoning && !validEfforts.includes(params.reasoningEffort)) {
      setParams({ reasoningEffort: 'medium' })
    }
  }, [showReasoning, validEfforts, params.reasoningEffort, setParams])

  // Close on Esc
  useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') close()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [open])

  const matchedPresetId = useMemo(() => {
    const p = PROMPT_PRESETS.find((pr) => pr.prompt === params.systemPrompt)
    return p?.id ?? 'custom'
  }, [params.systemPrompt])

  return (
    <AnimatePresence>
      {open && (
        <>
          {/* Backdrop (non-modal: chat stays interactive when clicking inside the panel) */}
          <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            transition={{ duration: 0.15 }}
            className="fixed inset-0 z-30 bg-black/40"
            onClick={close}
          />

          <motion.aside
            initial={{ x: 360, opacity: 0 }}
            animate={{ x: 0, opacity: 1 }}
            exit={{ x: 360, opacity: 0 }}
            transition={{ duration: 0.22, ease: 'easeOut' }}
            className="fixed top-0 right-0 bottom-0 z-40 w-[380px] max-w-[90vw]
                       bg-[#0F1020]/95 border-l border-white/10 backdrop-blur-xl
                       overflow-y-auto shadow-2xl shadow-black/60"
          >
            <header className="sticky top-0 z-10 flex items-center justify-between px-5 py-4
                               bg-[#0F1020]/90 backdrop-blur-xl border-b border-white/5">
              <div className="flex items-center gap-2">
                <Sparkles size={14} className="text-cyan-300" />
                <h2 className="text-sm font-semibold tracking-tight">Parameters</h2>
              </div>
              <div className="flex items-center gap-1">
                <button
                  onClick={resetParams}
                  title="Reset to defaults"
                  className="p-1.5 rounded-lg text-white/40 hover:text-white hover:bg-white/5 transition-colors"
                >
                  <RotateCcw size={14} />
                </button>
                <button
                  onClick={close}
                  className="p-1.5 rounded-lg text-white/40 hover:text-white hover:bg-white/5 transition-colors"
                >
                  <X size={14} />
                </button>
              </div>
            </header>

            <div className="px-5 py-4 space-y-6">
              {/* Preset selector */}
              <section className="space-y-2">
                <label className="text-[10px] uppercase tracking-wider text-white/40 font-medium">
                  Preset
                </label>
                <select
                  value={matchedPresetId === 'custom' ? '' : matchedPresetId}
                  onChange={(e) => {
                    const p = PROMPT_PRESETS.find((pr) => pr.id === e.target.value)
                    if (p) setParams({ systemPrompt: p.prompt })
                  }}
                  className="w-full px-3 py-2 rounded-lg bg-white/5 border border-white/10
                             text-sm text-white focus:outline-none focus:border-cyan-400/40"
                >
                  {matchedPresetId === 'custom' && (
                    <option value="" disabled>
                      Custom prompt
                    </option>
                  )}
                  {PROMPT_PRESETS.map((p) => (
                    <option key={p.id} value={p.id} className="bg-[#0F1020]">
                      {p.label}
                    </option>
                  ))}
                </select>
                <p className="text-[11px] text-white/40">
                  {PROMPT_PRESETS.find((p) => p.id === matchedPresetId)?.description ??
                    'Custom prompt — your edits are preserved.'}
                </p>
              </section>

              {/* System prompt textarea */}
              <section className="space-y-2">
                <div className="flex items-center justify-between">
                  <label className="text-[10px] uppercase tracking-wider text-white/40 font-medium">
                    System prompt
                  </label>
                  <span className="text-[10px] text-white/30 font-mono">
                    {params.systemPrompt.length} chars
                  </span>
                </div>
                <textarea
                  value={params.systemPrompt}
                  onChange={(e) => setParams({ systemPrompt: e.target.value })}
                  rows={7}
                  placeholder="You are a helpful AI assistant..."
                  className="w-full px-3 py-2 rounded-lg bg-white/5 border border-white/10
                             text-sm text-white placeholder:text-white/30
                             focus:outline-none focus:border-cyan-400/40
                             resize-y min-h-[120px] max-h-[400px] leading-relaxed"
                />
              </section>

              {/* Reasoning effort — Responses API only */}
              {showReasoning ? (
                <section className="space-y-2">
                  <label className="text-[10px] uppercase tracking-wider text-white/40 font-medium">
                    Reasoning effort
                  </label>
                  <div
                    className="grid gap-1 p-1 rounded-lg bg-white/5 border border-white/10"
                    style={{ gridTemplateColumns: `repeat(${validEfforts.length}, minmax(0, 1fr))` }}
                  >
                    {validEfforts.map((effort) => (
                      <button
                        key={effort}
                        onClick={() => setParams({ reasoningEffort: effort as ReasoningEffort })}
                        className={`px-2 py-1.5 rounded-md text-xs font-medium transition-colors ${
                          params.reasoningEffort === effort
                            ? 'bg-cyan-400/20 border border-cyan-300/30 text-cyan-200'
                            : 'text-white/60 hover:text-white hover:bg-white/5'
                        }`}
                      >
                        {effort}
                      </button>
                    ))}
                  </div>
                  <p className="text-[11px] text-white/40 leading-snug">
                    {params.reasoningEffort === 'minimal' &&
                      'Fastest, least thinking. Good for retrieval-style asks.'}
                    {params.reasoningEffort === 'low' && 'Light thinking; faster, lower cost.'}
                    {params.reasoningEffort === 'medium' &&
                      'Balanced default. Good for most tasks.'}
                    {params.reasoningEffort === 'high' &&
                      'Deepest thinking; more tokens, more cost, best reasoning.'}
                    {params.reasoningEffort === 'xhigh' &&
                      'Maximum thinking budget. Slowest; reserve for hard problems.'}
                  </p>
                  {selectedDeployment === 'gpt-5.4-pro' && (
                    <p className="text-[10px] text-white/30 italic">
                      gpt-5.4-pro only supports medium / high / xhigh.
                    </p>
                  )}
                  {params.reasoningEffort === 'xhigh' && (
                    <div className="mt-2 rounded-md border border-rose-400/25 bg-rose-500/[0.07] px-2.5 py-2">
                      <p className="text-[11px] text-rose-200/85 leading-snug">
                        <span className="font-semibold">Heads up — xhigh frequently produces no output.</span>{' '}
                        On complex prompts the model consumes the entire
                        output-token budget on internal reasoning, leaving
                        nothing for the answer. If you see "Model reasoned but
                        produced no output", drop to <span className="font-mono">high</span> and retry.
                      </p>
                    </div>
                  )}
                </section>
              ) : (
                <section className="space-y-1">
                  <label className="text-[10px] uppercase tracking-wider text-white/40 font-medium">
                    Reasoning effort
                  </label>
                  <p className="text-[11px] text-white/30 italic">
                    Not supported by this deployment (Chat Completions API).
                  </p>
                </section>
              )}

              {/* Max output tokens */}
              <section className="space-y-2">
                <div className="flex items-center justify-between">
                  <label className="text-[10px] uppercase tracking-wider text-white/40 font-medium">
                    Max output tokens
                  </label>
                  <input
                    type="number"
                    min={MAX_TOKENS_BOUNDS.min}
                    max={MAX_TOKENS_BOUNDS.max}
                    step={MAX_TOKENS_BOUNDS.step}
                    value={params.maxOutputTokens}
                    onChange={(e) => {
                      const v = Number(e.target.value)
                      if (!Number.isNaN(v)) {
                        setParams({
                          maxOutputTokens: Math.max(
                            MAX_TOKENS_BOUNDS.min,
                            Math.min(MAX_TOKENS_BOUNDS.max, v),
                          ),
                        })
                      }
                    }}
                    className="w-24 px-2 py-1 rounded-md bg-white/5 border border-white/10
                               text-xs text-white text-right font-mono
                               focus:outline-none focus:border-cyan-400/40"
                  />
                </div>
                <input
                  type="range"
                  min={MAX_TOKENS_BOUNDS.min}
                  max={MAX_TOKENS_BOUNDS.max}
                  step={MAX_TOKENS_BOUNDS.step}
                  value={params.maxOutputTokens}
                  onChange={(e) => setParams({ maxOutputTokens: Number(e.target.value) })}
                  className="w-full accent-cyan-400"
                />
                <div className="flex justify-between text-[10px] text-white/30 font-mono">
                  <span>{MAX_TOKENS_BOUNDS.min.toLocaleString()}</span>
                  <span>{MAX_TOKENS_BOUNDS.max.toLocaleString()}</span>
                </div>
              </section>

              <div className="pt-2 border-t border-white/5 text-[11px] text-white/30 text-center">
                Changes apply to the next message
              </div>
            </div>
          </motion.aside>
        </>
      )}
    </AnimatePresence>
  )
}
