/**
 * Typed fetch wrappers for the AOAI Chat backend.
 * In dev: requests are proxied via Vite from :5173 → :8765.
 * In prod: served from the same FastAPI process on :8765.
 */

export interface AppConfig {
  endpoint: string
  default_deployment: string
  known_deployments: string[]
  api_version: string
  save_dir: string
  theme: string
  token_scope: string
  configured: boolean
}

export interface Deployment {
  id: string
  model: string
  supports_responses_api: boolean
}

export interface DeploymentsResponse {
  count: number
  default: string
  data: Deployment[]
}

export interface ConversationSummary {
  id: string
  title: string
  created_at: string
  updated_at: string
  deployment: string
  turn_count: number
  usage_total: Record<string, number>
  filename: string
}

export interface Turn {
  role: 'user' | 'assistant' | 'system'
  content: string
  timestamp: string
  deployment: string | null
  response_id: string | null
  tokens: number | null
}

export interface ConversationDetail extends ConversationSummary {
  endpoint: string
  response_id: string | null
  turns: Turn[]
}

async function jfetch<T>(input: RequestInfo, init?: RequestInit): Promise<T> {
  const r = await fetch(input, init)
  if (!r.ok) {
    let body: unknown = undefined
    try { body = await r.json() } catch { body = await r.text() }
    throw new ApiError(r.status, body)
  }
  return r.json() as Promise<T>
}

export class ApiError extends Error {
  constructor(public status: number, public body: unknown) {
    super(`API error ${status}: ${typeof body === 'string' ? body : JSON.stringify(body).slice(0, 200)}`)
  }
}

export const api = {
  health: () => jfetch<{ status: string; version: string }>('/api/health'),

  getConfig: () => jfetch<AppConfig>('/api/config'),
  putConfig: (patch: Partial<AppConfig>) =>
    jfetch<AppConfig>('/api/config', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(patch),
    }),
  testAuth: () =>
    jfetch<{ ok: boolean; scope: string; token_prefix: string; token_len: number }>(
      '/api/config/test-auth',
      { method: 'POST' },
    ),

  listDeployments: () => jfetch<DeploymentsResponse>('/api/deployments'),
  addDeployment: (name: string) =>
    jfetch<DeploymentsResponse & { added: string }>('/api/deployments', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name }),
    }),
  removeDeployment: (name: string) =>
    jfetch<DeploymentsResponse & { removed: string }>(
      `/api/deployments/${encodeURIComponent(name)}`,
      { method: 'DELETE' },
    ),

  listConversations: () =>
    jfetch<{ count: number; data: ConversationSummary[]; save_dir: string }>('/api/conversations'),
  getConversation: (id: string) =>
    jfetch<ConversationDetail>(`/api/conversations/${encodeURIComponent(id)}`),
  deleteConversation: (id: string) =>
    jfetch<{ deleted: string; filename: string }>(
      `/api/conversations/${encodeURIComponent(id)}`,
      { method: 'DELETE' },
    ),
}
