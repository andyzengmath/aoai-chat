import * as Dialog from '@radix-ui/react-dialog'
import { Check, Plus, Trash2, X } from 'lucide-react'
import { useEffect, useState } from 'react'

import { useChatStore } from '../store/chatStore'

export function SettingsDialog() {
  const open = useChatStore((s) => s.settingsOpen)
  const openSettings = useChatStore((s) => s.openSettings)
  const config = useChatStore((s) => s.config)
  const deployments = useChatStore((s) => s.deployments)
  const putConfig = useChatStore((s) => s.putConfig)
  const addDeployment = useChatStore((s) => s.addDeployment)
  const removeDeployment = useChatStore((s) => s.removeDeployment)
  const setToast = useChatStore((s) => s.setToast)
  const streaming = useChatStore((s) => s.streaming)
  const deploymentMutationInFlight = useChatStore(
    (s) => s.deploymentMutationInFlight,
  )

  const [endpoint, setEndpoint] = useState('')
  const [newDeployment, setNewDeployment] = useState('')
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    if (open && config) setEndpoint(config.endpoint || '')
  }, [open, config])

  const saveEndpoint = async () => {
    if (!endpoint.trim()) return
    setSaving(true)
    try {
      await putConfig({ endpoint: endpoint.trim() })
      setToast({ kind: 'success', message: 'Endpoint saved.' })
    } catch (e) {
      setToast({ kind: 'error', message: (e as Error).message })
    } finally {
      setSaving(false)
    }
  }

  const submitAdd = async () => {
    const name = newDeployment.trim()
    if (!name) return
    try {
      await addDeployment(name)
      setNewDeployment('')
    } catch (e) {
      setToast({ kind: 'error', message: (e as Error).message })
    }
  }

  return (
    <Dialog.Root open={open} onOpenChange={openSettings}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 surface-overlay z-40" />
        <Dialog.Content
          className="fixed left-1/2 top-1/2 -translate-x-1/2 -translate-y-1/2 z-50
                     w-[min(560px,calc(100vw-2rem))] max-h-[85vh] overflow-y-auto
                     rounded-2xl border border-white/10 surface-elevated p-6"
        >
          <div className="flex items-center justify-between mb-6">
            <Dialog.Title className="text-lg font-semibold tracking-tight">Settings</Dialog.Title>
            <Dialog.Close asChild>
              <button className="p-1.5 rounded-lg text-white/40 hover:text-white hover:bg-white/5 transition-colors">
                <X size={16} />
              </button>
            </Dialog.Close>
          </div>

          {/* Endpoint */}
          <section className="space-y-2 mb-6">
            <label className="block text-xs font-medium text-white/60">
              Azure OpenAI endpoint
            </label>
            <div className="flex gap-2">
              <input
                value={endpoint}
                onChange={(e) => setEndpoint(e.target.value)}
                placeholder="https://your-resource.openai.azure.com/"
                className="flex-1 px-3 py-2 rounded-lg bg-white/5 border border-white/10
                           text-sm text-white placeholder:text-white/30
                           focus:outline-none focus:border-cyan-400/40 font-mono"
              />
              <button
                onClick={saveEndpoint}
                disabled={saving || !endpoint.trim()}
                className="px-4 py-2 rounded-lg bg-cyan-400/20 hover:bg-cyan-400/30
                           border border-cyan-300/30 text-sm text-cyan-300
                           disabled:opacity-40 transition-colors"
              >
                {saving ? '…' : 'Save'}
              </button>
            </div>
            {config?.configured ? (
              <div className="flex items-center gap-1.5 text-xs text-emerald-400/80">
                <Check size={12} />
                Configured · save dir: <span className="font-mono text-white/40">{config.save_dir}</span>
              </div>
            ) : (
              <div className="text-xs text-amber-300/80">
                Paste your endpoint URL and click Save. Then run <span className="font-mono">az login</span> if you haven't.
              </div>
            )}
          </section>

          {/* Deployments */}
          <section className="space-y-2 mb-6">
            <label className="block text-xs font-medium text-white/60">
              Known deployments
            </label>
            <p className="text-xs text-white/30">
              Azure OpenAI doesn't expose deployment enumeration on the data plane. List the
              deployment names you've created here.
            </p>
            <div className="space-y-1.5">
              {deployments.map((d) => (
                <div
                  key={d.id}
                  className="flex items-center justify-between gap-2 px-3 py-2
                             rounded-lg bg-white/5 border border-white/10"
                >
                  <div className="flex flex-col">
                    <span className="font-mono text-sm">{d.id}</span>
                    <span className="text-[10px] text-white/40">
                      {d.supports_responses_api ? 'Responses API' : 'Chat Completions'}
                      {d.model_version ? ` · ${d.model_version}` : ''}
                      {d.context_window_tokens
                        ? ` · ${Number((d.context_window_tokens / 1_000_000).toFixed(2))}M context`
                        : ''}
                      {d.reasoning_modes.includes('pro') ? ' · Pro mode' : ''}
                    </span>
                  </div>
                  <button
                    onClick={() => removeDeployment(d.id)}
                    disabled={!!streaming || deploymentMutationInFlight}
                    className="text-white/30 hover:text-red-400 transition-colors
                               disabled:opacity-30 disabled:cursor-not-allowed"
                    title="Remove"
                  >
                    <Trash2 size={14} />
                  </button>
                </div>
              ))}
            </div>
            <div className="flex gap-2 pt-1">
              <input
                value={newDeployment}
                onChange={(e) => setNewDeployment(e.target.value)}
                onKeyDown={(e) => e.key === 'Enter' && submitAdd()}
                placeholder="deployment name (e.g. gpt-5.6-sol)"
                className="flex-1 px-3 py-2 rounded-lg bg-white/5 border border-white/10
                           text-sm text-white placeholder:text-white/30
                           focus:outline-none focus:border-cyan-400/40 font-mono"
              />
              <button
                onClick={submitAdd}
                disabled={
                  !newDeployment.trim()
                  || !!streaming
                  || deploymentMutationInFlight
                }
                className="px-3 py-2 rounded-lg bg-white/5 hover:bg-white/10
                           border border-white/10 text-sm text-white/80
                           disabled:opacity-40 transition-colors flex items-center gap-1"
              >
                <Plus size={14} />
                Add
              </button>
            </div>
          </section>

          <Dialog.Close asChild>
            <button className="w-full py-2 rounded-lg bg-white/5 hover:bg-white/10 border border-white/10 text-sm text-white/80 transition-colors">
              Done
            </button>
          </Dialog.Close>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  )
}
