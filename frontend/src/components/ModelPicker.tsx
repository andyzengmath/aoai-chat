import * as DropdownMenu from '@radix-ui/react-dropdown-menu'
import clsx from 'clsx'
import { Check, ChevronDown, Sparkles } from 'lucide-react'

import { useChatStore } from '../store/chatStore'

export function ModelPicker() {
  const deployments = useChatStore((s) => s.deployments)
  const selected = useChatStore((s) => s.selectedDeployment)
  const setDeployment = useChatStore((s) => s.setDeployment)
  const streaming = useChatStore((s) => s.streaming)
  const deploymentMutationInFlight = useChatStore(
    (s) => s.deploymentMutationInFlight,
  )

  const current = deployments.find((d) => d.id === selected)

  return (
    <DropdownMenu.Root>
      <DropdownMenu.Trigger asChild>
        <button
          disabled={!!streaming || deploymentMutationInFlight}
          title={
            streaming
              ? 'Stop generation before switching deployments'
              : deploymentMutationInFlight
                ? 'Wait for the deployment update to finish'
                : undefined
          }
          className="flex items-center gap-2 px-3 py-1.5 rounded-lg
                     bg-white/5 hover:bg-white/10 border border-white/10
                     text-sm text-white/80 transition-colors
                     disabled:opacity-50 disabled:cursor-not-allowed disabled:hover:bg-white/5"
        >
          <Sparkles size={14} className="text-cyan-300" />
          <span className="font-mono">{selected || 'no deployment'}</span>
          <ChevronDown size={14} className="text-white/40" />
        </button>
      </DropdownMenu.Trigger>

      <DropdownMenu.Portal>
        <DropdownMenu.Content
          align="start"
          sideOffset={4}
          className="min-w-[240px] rounded-xl border border-white/10 surface-elevated p-1"
        >
          {deployments.length === 0 && (
            <div className="px-3 py-3 text-xs text-white/40">
              No deployments configured. Add one in Settings.
            </div>
          )}
          {deployments.map((d) => (
            <DropdownMenu.Item
              key={d.id}
              onClick={() => setDeployment(d.id)}
              className={clsx(
                'flex items-center justify-between gap-2 px-3 py-2 rounded-lg cursor-pointer',
                'text-sm outline-none',
                d.id === selected ? 'bg-white/10 text-white' : 'text-white/70 hover:bg-white/5',
              )}
            >
              <div className="flex flex-col">
                <span className="font-mono">{d.id}</span>
                <span className="text-[10px] text-white/40">
                  {d.supports_responses_api ? 'Responses API' : 'Chat Completions'}
                  {d.model_version ? ` · ${d.model_version}` : ''}
                  {d.context_window_tokens
                    ? ` · ${Number((d.context_window_tokens / 1_000_000).toFixed(2))}M ctx`
                    : ''}
                  {d.reasoning_modes.includes('pro') ? ' · Pro' : ''}
                </span>
              </div>
              {d.id === selected && <Check size={14} className="text-cyan-300" />}
            </DropdownMenu.Item>
          ))}
          {current && (
            <div className="px-3 py-2 border-t border-white/5 mt-1 text-[10px] text-white/30 font-mono">
              api: {current.supports_responses_api ? '/openai/v1/responses' : '/openai/v1/chat/completions'}
            </div>
          )}
        </DropdownMenu.Content>
      </DropdownMenu.Portal>
    </DropdownMenu.Root>
  )
}
