# AOAI Chat

A lightweight, futuristic-looking ChatGPT-style client for Azure OpenAI — built because the Foundry Playground is hard to use as a daily driver.

- **Managed-identity auth** via `DefaultAzureCredential` — no API keys in code
- **Responses API** with `previous_response_id` chaining + server-side compaction (for 1M-context performance)
- **GPT-5.6** with `max` effort and independent Standard/Pro reasoning modes
- **Chat Completions fallback** for any deployment that doesn't expose Responses
- **Streaming** via Server-Sent Events with reasoning-summary ticker, freshness indicator, and Stop button
- **Inline LaTeX** rendering (KaTeX) and GitHub-flavored Markdown with code highlighting
- **Transcripts** saved locally as `.md` files in `conversations/`
- **iOS 26 "Liquid Glass" UI** — translucent surfaces, deep black canvas, Onest typeface, cosmic accents
- **Terminal-style history** — ↑ / ↓ in the input bar recall previous prompts
- **Stable native scrolling** with bottom pinning for long, variable-height Markdown
- **Offscreen message containment** keeps long Markdown/KaTeX histories responsive
- **Two-port dev**, **one-port prod** — Vite proxy → FastAPI in dev, FastAPI serves built bundle in prod

## Prerequisites

- **Python 3.11+** (uv will fetch one if needed) and [`uv`](https://docs.astral.sh/uv/)
- **Node.js 20+** with `npm`
- **Azure CLI** — run `az login` before first use
- An **Azure OpenAI resource** with deployments, and the `Cognitive Services OpenAI User` role on your account

## Quickstart

```powershell
# 1. Sign in to Azure
az login

# 2. Install deps
cd backend  ; uv sync ; cd ..
cd frontend ; npm install ; cd ..

# 3. Launch dev servers (backend :8765 + Vite :5173 in parallel)
./scripts/dev.ps1
```

Open <http://localhost:5173> → click **Settings** (top-right or via ⌘K → Settings) → paste your AOAI endpoint URL → pick a deployment from the model picker → start chatting.

## Production-style single-port run

```powershell
cd frontend ; npm run build ; cd ..
cd backend  ; uv run aoai-chat
```

FastAPI now serves the built frontend at <http://localhost:8765>.

## Layout

```
backend/            FastAPI + azure-identity + openai SDK
  app/
    main.py         Entry point, static mount, no-cache middleware
    auth.py         Token provider (ai.azure.com → cognitiveservices fallback)
    settings.py     Config persistence (.aoai-chat/config.json + env)
    aoai_client.py  Responses API + Chat Completions dual-path streaming
    transcript.py   Markdown writer with YAML frontmatter
    routes/
      config.py
      deployments.py
      chat.py        SSE stream
      conversations.py
  tests/            pytest (97 cases)
frontend/           React + Vite + TS + Tailwind v4
  src/
    api/            client + SSE consumer
    store/          Zustand store with rAF-throttled streaming
    components/     ChatPane, StreamingBubble, ParametersPanel, ...
    markdown/       react-markdown wrapper + lazy CodeBlock
    config/         Prompt presets, pondering phrases, example chips
scripts/dev.ps1     Parallel uv + npm dev runner
conversations/      (gitignored) .md transcripts
.aoai-chat/         (gitignored) config.json
.omc/plans/         Architecture & implementation plan
```

## Keyboard shortcuts

| Keys | Action |
|---|---|
| <kbd>⌘K</kbd> / <kbd>Ctrl+K</kbd> | Command palette (switch conversation, new chat, settings) |
| <kbd>Enter</kbd> | Send message |
| <kbd>Shift+Enter</kbd> | Newline in input |
| <kbd>↑</kbd> / <kbd>↓</kbd> | Terminal-style history (previous / next prompt; caret must be on first / last line for multi-line inputs) |

## Testing

```powershell
cd backend
uv sync --extra dev
uv run pytest -v
```

Backend tests cover model capabilities, reasoning-mode validation,
background-stream recovery and cancellation, transcript round-trip, atomic
writes, frontmatter shape, and conversation indexing.

## GPT-5.6, Pro mode, and context limits

The default deployment is `gpt-5.6-sol`. The verified Azure deployment uses model
version `2026-07-09` on `GlobalStandard`.

### Documented limits (Azure docs, July 2026)

| Model | Context window | Maximum input | Maximum output |
|---|---:|---:|---:|
| `gpt-5.6-sol` (`2026-07-09`) | 1,050,000 | 922,000 | 128,000 |
| `gpt-5.4-pro` | 1,050,000 | 922,000 | 128,000 |

The Parameters panel gets these limits from the backend capability contract and
caps `max_output_tokens` at the deployment's documented 128,000-token ceiling.

### Reasoning effort and mode are independent

`gpt-5.6-sol` accepts `none`, `low`, `medium`, `high`, `xhigh`, and `max`.
Higher effort can improve difficult work at the cost of latency and output tokens.

| Effort | Intended use |
|---|---|
| `none` / `low` | Latency-sensitive retrieval and routine tasks |
| `medium` | Balanced default for coding, analysis, and planning |
| `high` | Difficult debugging and complex workflows |
| `xhigh` | Extended research and long-running hard problems |
| `max` | Maximum single-agent reasoning for quality-first tasks |

Pro is **not** a separate deployment and is **not** a reasoning effort. It is
`reasoning.mode: "pro"` on the same `gpt-5.6-sol` deployment. Standard/Pro mode
and effort can be selected independently, including Pro + `max`. Pro performs
additional model work and can substantially increase latency and token usage.
Selecting `max` effort does not silently change `max_output_tokens`; when the
configured budget is below the deployment limit, the Parameters panel shows
the constraint and provides a one-click **Use 128K** action.

Sources: [Azure model limits](https://learn.microsoft.com/azure/foundry/openai/how-to/reasoning)
and [OpenAI reasoning modes](https://developers.openai.com/api/docs/guides/reasoning#reasoning-mode).

### Why `xhigh` can produce no output on `gpt-5.4-pro`

With `max_output_tokens = 65,536` and a hard prompt:

- `xhigh` consumes ~60,000+ tokens on internal reasoning
- That leaves <5,000 tokens for the actual answer
- For long markdown / LaTeX responses, that's not enough
- The model hits the cap mid-reasoning → `response.completed` fires with `status: "incomplete"`
- Zero `output_text.delta` events ever arrive
- The app saves the response ID and any partial output, then shows
  **Response incomplete** with a resumable **Continue** action

**Rule of thumb for 5.4 Pro:** stay on `high` for almost everything. Escalate to
`xhigh` only when evaluations show a clear benefit, and leave enough of the
128,000-token output budget for the visible answer.

### `gpt-5.4-pro` supported efforts

This deployment **only accepts `medium` / `high` / `xhigh`**. The backend
publishes per-deployment capabilities; the Parameters panel renders only the
supported choices and does not offer Pro mode for 5.4 Pro.

## Reliability features

The chat path has multiple guard rails for long-running reasoning sessions:

| Guard | Threshold | What it catches |
|---|---|---|
| `httpx.read` timeout | 30 min between chunks | Network/proxy stalls |
| Background-stream recovery | Premature SSE termination | Polls the stored response and delivers its completed output |
| Safe server-error retry | Terminal zero-output `server_error` | Retries twice in-stream after 2s/5s; never retries ambiguous create failures or partial output |
| Background cancellation | Stop/watchdog | Uses a per-stream cancellation grant to cancel the Azure response before aborting local SSE |
| Incomplete continuation | `max_output_tokens` | Saves partial output and response state, restores it after reload, then offers a chained Continue action |
| Stale-event watchdog | 30 min without any SSE event | Dead Azure-side request |
| No-content watchdog | 60 min total elapsed with zero `output_text.delta` | Reasoning that never produces output |
| Empty-response guard | After `done` with no visible output | Unexpected empty completions |
| User-facing **Stop** button | Manual | Escape hatch any time |
| Inline error card with **Retry** | Persistent UI | No silent failures |

All automatic guards reset/skip on a healthy stream, so you don't lose legitimate long thoughts (~30-60 min reasoning is fine on `high`).

## Troubleshooting

| Symptom | Fix |
|---|---|
| Backend returns 401 / `az_login_required` | Run `az login` and refresh. |
| Deployments dropdown empty | Add your deployment names in Settings — Azure OpenAI doesn't expose deployment enumeration on the data plane. |
| `Responses API 404` toast / "operation is unsupported" | The deployment doesn't expose Responses; the app auto-falls-back to Chat Completions. |
| `2025-01-01-preview` does not support Responses API | App calls `/openai/v1/*` directly (no api-version), so this only matters for older Chat Completions paths. |
| Tokens don't stream live | Corporate proxy may buffer SSE; try a direct connection (FastAPI sends `X-Accel-Buffering: no`). |
| Equations show as raw `$...$` | Hard-refresh — KaTeX CSS is bundled. |
| "Response incomplete" / `max_output_tokens` | Use **Continue** for a fresh chained budget; optionally select **Use 128K** first. |
| "Model reasoned but produced no output" | Open Parameters → select **Use 128K** or lower the effort, then Retry. |
| Browser stuck on old bundle after a deploy | Server sends `Cache-Control: no-cache` on HTML. If you somehow still see stale UI, hard-refresh once (Ctrl+Shift+R). |
| Stream stuck for hours | Watchdogs auto-abort at 30/60 min; if you want sooner, click the ■ STOP button in the streaming bubble. |

## Environment variables

Copy `.env.example` → `.env` and tweak:

```
AOAI_ENDPOINT=https://your-resource.openai.azure.com/
AOAI_DEPLOYMENT=gpt-5.6-sol
AOAI_PORT=8765
AOAI_HOST=127.0.0.1
AOAI_OPEN_BROWSER=1
AOAI_READ_TIMEOUT=1800       # httpx read between chunks (s)
AOAI_CONNECT_TIMEOUT=15
AOAI_CANCEL_TIMEOUT=10       # bounded remote cleanup (s)
```

`ENDPOINT_URL` and `DEPLOYMENT_NAME` are accepted as aliases for compatibility
with Microsoft Foundry sample code. The `AOAI_*` names take precedence when
both forms are set.

Endpoints must use HTTPS and an official Azure OpenAI/AI Services hostname.
`AOAI_TOKEN_SCOPE` may be left empty for cloud-aware automatic selection.
Explicit scopes must match the endpoint cloud: public Azure accepts
`https://ai.azure.com/.default` or
`https://cognitiveservices.azure.com/.default`; Azure Government and China
use their corresponding `cognitiveservices.azure.us` or
`cognitiveservices.azure.cn` audience.

## Plan

See [`.omc/plans/aoai-chat-app.md`](.omc/plans/aoai-chat-app.md) for the full architecture and phase-by-phase breakdown.
