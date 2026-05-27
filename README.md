# AOAI Chat

A lightweight, futuristic-looking ChatGPT-style client for Azure OpenAI — built because the Foundry Playground is hard to use as a daily driver.

- **Managed-identity auth** via `DefaultAzureCredential` — no API keys in code
- **Responses API** with `previous_response_id` chaining + server-side compaction (for 1M-context performance)
- **Chat Completions fallback** for any deployment that doesn't expose Responses
- **Streaming** via Server-Sent Events with reasoning-summary ticker, freshness indicator, and Stop button
- **Inline LaTeX** rendering (KaTeX) and GitHub-flavored Markdown with code highlighting
- **Transcripts** saved locally as `.md` files in `conversations/`
- **iOS 26 "Liquid Glass" UI** — translucent surfaces, deep black canvas, Onest typeface, cosmic accents
- **Terminal-style history** — ↑ / ↓ in the input bar recall previous prompts
- **Virtualized** message list — handles huge conversations without slowdown
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
  tests/            pytest (29 cases)
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

29 tests cover capability detection (`supports_responses_api`), transcript round-trip, atomic writes, frontmatter shape, and conversation indexing.

## Notes on `gpt-5.4-pro` (and the `xhigh` failure mode)

`gpt-5.4-pro` and `gpt-5.5` are **Responses-API-only** models — Chat Completions returns `400 unsupported` for them. The app routes correctly because both are in `KNOWN_RESPONSES_MODELS` in `backend/app/aoai_client.py`.

### Documented limits (per Azure docs, as of 2026-05)

| Model | Context window | Max output tokens (reasoning + text combined) |
|---|---|---|
| `gpt-5.4-pro` | 1,050,000 | 128,000 |
| `gpt-5.5` | 1,050,000 | 128,000 |
| `gpt-5.4` / `5.4-mini` / `5.4-nano` | 1,050,000 / 400,000 / 400,000 | 128,000 |
| `gpt-5-pro` | 400,000 | 128,000 |
| `gpt-5` / `-mini` / `-nano` | 400,000 | 128,000 |

The API does **not** reject oversized `max_output_tokens` — passing 1,000,000 is accepted but internally clamped to the deployment cap. Setting the slider to its max (131,072) is safe and recommended for reasoning-heavy prompts.

### Reasoning effort — what it actually does

`reasoning_effort` controls how many **internal reasoning tokens** the model uses. Those tokens count toward `max_output_tokens` together with the visible output text. Effort levels approximate (empirical):

| Effort | Typical reasoning tokens (hard prompt) |
|---|---|
| `minimal` | 200 – 1,000 |
| `low` | 1,000 – 5,000 |
| `medium` | 5,000 – 15,000 |
| `high` | 15,000 – 50,000 |
| `xhigh` | 50,000 – 200,000+ (essentially uncapped) |

### Why `xhigh` often produces no output

With `max_output_tokens = 65,536` and a hard prompt:

- `xhigh` consumes ~60,000+ tokens on internal reasoning
- That leaves <5,000 tokens for the actual answer
- For long markdown / LaTeX responses, that's not enough
- The model hits the cap mid-reasoning → `response.completed` fires with `status: "incomplete"`
- Zero `output_text.delta` events ever arrive
- UI shows the "No response" inline error

**Rule of thumb:** stay on `high` for almost everything. Escalate to `xhigh` only when `high` is visibly under-thinking, and pair it with the slider maxed at 131,072. The Parameters panel surfaces a rose-tinted warning card when `xhigh` is selected.

### `gpt-5.4-pro` supported efforts

This deployment **only accepts `medium` / `high` / `xhigh`** (not `minimal` / `low`). The `KNOWN_RESPONSES_MODELS` lookup in the Parameters panel enforces this — the segmented control hides the unsupported buttons for this model.

## Reliability features

The chat path has multiple guard rails for long-running reasoning sessions:

| Guard | Threshold | What it catches |
|---|---|---|
| `httpx.read` timeout | 30 min between chunks | Network/proxy stalls |
| Stale-event watchdog | 5 min without any SSE event | Dead Azure-side request |
| No-content watchdog | 20 min total elapsed with zero `output_text.delta` | Reasoning that never produces output |
| Empty-response guard | After `done` event with `assembled === ''` | Budget-exhausted reasoning |
| User-facing **Stop** button | Manual | Escape hatch any time |
| Inline error card with **Retry** | Persistent UI | No silent failures |

All four automatic guards reset/skip on a healthy stream, so you don't lose legitimate long thoughts (~30-60 min reasoning is fine on `high`).

## Troubleshooting

| Symptom | Fix |
|---|---|
| Backend returns 401 / `az_login_required` | Run `az login` and refresh. |
| Deployments dropdown empty | Add your deployment names in Settings — Azure OpenAI doesn't expose deployment enumeration on the data plane. |
| `Responses API 404` toast / "operation is unsupported" | The deployment doesn't expose Responses; the app auto-falls-back to Chat Completions. |
| `2025-01-01-preview` does not support Responses API | App calls `/openai/v1/*` directly (no api-version), so this only matters for older Chat Completions paths. |
| Tokens don't stream live | Corporate proxy may buffer SSE; try a direct connection (FastAPI sends `X-Accel-Buffering: no`). |
| Equations show as raw `$...$` | Hard-refresh — KaTeX CSS is bundled. |
| "Model reasoned but produced no output" | Open Parameters → set Max Output Tokens to 131,072 and drop effort from `xhigh` to `high`. Then Retry. |
| Browser stuck on old bundle after a deploy | Server sends `Cache-Control: no-cache` on HTML. If you somehow still see stale UI, hard-refresh once (Ctrl+Shift+R). |
| Stream stuck for hours | Watchdogs auto-abort at 5/20 min; if you want sooner, click the ■ STOP button in the streaming bubble. |

## Environment variables

Copy `.env.example` → `.env` and tweak:

```
AOAI_ENDPOINT=https://your-resource.openai.azure.com/
AOAI_DEPLOYMENT=gpt-5.4-pro
AOAI_PORT=8765
AOAI_HOST=127.0.0.1
AOAI_OPEN_BROWSER=1
AOAI_READ_TIMEOUT=1800       # httpx read between chunks (s)
AOAI_CONNECT_TIMEOUT=15
```

## Plan

See [`.omc/plans/aoai-chat-app.md`](.omc/plans/aoai-chat-app.md) for the full architecture and phase-by-phase breakdown.
