# AOAI Chat — Lightweight Web App for Azure OpenAI

A local-first ChatGPT-style client for Azure OpenAI with Entra ID auth, Responses API support, local Markdown persistence, inline LaTeX, and 1M-context performance.

---

## 1. Requirements Summary

| # | Requirement | Resolution |
|---|---|---|
| R1 | Managed-identity login (no API key) | `DefaultAzureCredential` + `get_bearer_token_provider` in backend; falls through `az login` → VS Code → IMDS automatically. |
| R2 | Configure endpoint, then pick model from it | Settings UI persists endpoint to `<repo>/.aoai-chat/config.json`; backend lists deployments via `GET /openai/deployments?api-version=2024-10-21` and populates dropdown. |
| R3 | Support **Responses API** for `gpt-5.4-pro` and `gpt-5.5` | Use unified `OpenAI(base_url=".../openai/v1/", api_key=token_provider)` with `client.responses.create(...)`, `previous_response_id` chaining, and `stream=True`. `gpt-5.4-pro` and `gpt-5.5` are hardcoded in `KNOWN_RESPONSES_MODELS` for capability detection. Auto-fallback to Chat Completions for any other deployment. |
| R4 | Persist transcript locally as Markdown | Backend writes one `.md` per conversation to `<repo>/conversations/YYYY-MM-DD-HHMMSS-<slug>.md`, with frontmatter (model, endpoint, response_id, token usage) and `## User` / `## Assistant` blocks. |
| R5 | Inline LaTeX rendering | KaTeX via `rehype-katex` + `remark-math` in `react-markdown`. Supports `$inline$` and `$$block$$`. |
| R6 | Future-like UI | Glass / "liquid glass" panels, deep dark + neon-cyan accent, subtle aurora gradient, micro-interactions, Inter + JetBrains Mono. Built per `frontend-design` skill principles. |
| R7 | 1M-context, no slowness | (a) **Server-side compaction** via `context_management={"type":"compaction","compact_threshold":200000}` so AOAI does the heavy lifting; (b) **virtualized** message list (`react-virtuoso`); (c) **streaming message rendered in a sibling component** so historical list never re-renders during token stream; (d) memoized markdown per-message; (e) lazy syntax highlighting via IntersectionObserver. |

Out of scope (v1): multi-user, cloud deployment, image input, tools/function calling, voice. Scaffolded as feature flags for v2.

---

## 2. Architecture

```
┌──────────────────────────────────────────────────────────────────┐
│  Browser (React + Vite + TS + Tailwind)                          │
│  ─ ChatPane (virtualized) ─ StreamingPane ─ Sidebar ─ Settings   │
│         │  SSE (EventSource)              │  fetch JSON           │
└─────────┼─────────────────────────────────┼───────────────────────┘
          ▼                                 ▼
┌──────────────────────────────────────────────────────────────────┐
│  FastAPI (localhost:8765)                                        │
│  /api/config  /api/deployments  /api/chat (SSE)                  │
│  /api/conversations  /api/conversations/{id}                     │
│      │                                  │                        │
│      ▼                                  ▼                        │
│  AOAIClient                       TranscriptStore                │
│  (azure-identity +                (writes .md, indexes folder)   │
│   openai SDK v1)                                                 │
└──────────────────────────────────────────────────────────────────┘
                       │
                       ▼
              Azure OpenAI Resource
        (Responses API or Chat Completions)
```

**Why one backend + one frontend** (not browser-only): managed identity requires server-side token acquisition (browsers can't run `DefaultAzureCredential`), and local file persistence requires server-side `fs` access. Backend runs on `localhost`, no auth between browser↔backend (single-user, local).

**Process model**: one binary entry point `aoai-chat` that:
1. Boots FastAPI on a free port (default 8765).
2. Serves the built React bundle at `/`.
3. Opens the user's default browser.
4. Single-port = no CORS in production; Vite dev proxy in dev.

---

## 3. Technology Choices (with rationale)

| Layer | Choice | Why this, not alternatives |
|---|---|---|
| Backend | **Python 3.11 + FastAPI + Uvicorn** | Best-in-class Azure SDKs (`azure-identity`, `openai`); SSE via `sse-starlette`; async-native. Node would require `@azure/identity` which is fine but the OpenAI Python SDK has more Responses API features today. |
| Auth | **`DefaultAzureCredential` + `get_bearer_token_provider`** | Per current MS docs. Works with `az login`, VS Code creds, managed identity (when deployed to Azure), workload identity. |
| AOAI SDK | **`openai>=1.55` `OpenAI()` client w/ v1 base_url** | The Responses API doc explicitly recommends this pattern (`base_url=".../openai/v1/"`, `api_key=token_provider`). Cleaner than the legacy `AzureOpenAI` class and required for some new Responses features. |
| Frontend | **React 18 + Vite + TypeScript** | Mature ecosystem for chat UIs; fast HMR; first-class virtualization libs. |
| Styling | **Tailwind CSS v4 + Radix Primitives** | Utility-first matches glass/futuristic look without huge CSS files. Radix for accessible primitives (Dialog, DropdownMenu). |
| State | **Zustand** | Lightweight, no boilerplate, no provider hell. Avoid Redux Toolkit (overkill). |
| Markdown | **`react-markdown` + `remark-gfm` + `remark-math` + `rehype-katex` + `rehype-highlight`** | Composable; KaTeX is 5–8× faster than MathJax. |
| Virtualization | **`react-virtuoso`** | Best chat-UX support: maintains scroll position on prepend, handles variable heights, smooth on 10k+ items. `react-window` lacks these. |
| Streaming transport | **SSE (`sse-starlette` ↔ `EventSource`)** | Unidirectional + reconnect built-in; simpler than WebSocket. Matches AOAI's own event model. |
| Persistence | **Plain `.md` files** | User-readable, version-controllable, no DB. Index built in-memory at startup from folder scan; cached. |
| Packaging | **`uv` for Python + `npm` for JS** | Both 10× faster than pip/npm. |

---

## 4. Repo Layout

```
aoai_chat/
├── pyproject.toml                  # uv-managed
├── README.md
├── .gitignore                      # ignore .aoai-chat/, conversations/, .venv, node_modules
├── .env.example                    # AOAI_ENDPOINT, AOAI_DEPLOYMENT, AOAI_PORT
├── .aoai-chat/                     # gitignored: config.json (endpoint, save_dir, theme)
├── conversations/                  # gitignored: <date>-<slug>.md transcripts
├── .omc/plans/aoai-chat-app.md     # this file
│
├── backend/
│   ├── app/
│   │   ├── main.py                 # FastAPI app, static mount, browser-open
│   │   ├── settings.py             # pydantic-settings + ~/.aoai-chat/config.json
│   │   ├── auth.py                 # token provider factory
│   │   ├── aoai_client.py          # OpenAI()-v1 wrapper, Responses + Chat fallback
│   │   ├── routes/
│   │   │   ├── config.py           # GET/PUT /api/config
│   │   │   ├── deployments.py      # GET /api/deployments
│   │   │   ├── chat.py             # POST /api/chat  (SSE)
│   │   │   └── conversations.py    # GET list, GET/PUT one, DELETE
│   │   ├── transcript.py           # Markdown writer/reader
│   │   └── schemas.py              # Pydantic models
│   └── tests/                      # pytest
│
├── frontend/
│   ├── package.json                # npm-managed
│   ├── vite.config.ts              # proxy /api → localhost:8765 in dev
│   ├── tailwind.config.ts
│   ├── index.html
│   └── src/
│       ├── main.tsx
│       ├── App.tsx                 # routes: /, /settings, /c/:id
│       ├── store/chatStore.ts      # zustand
│       ├── api/client.ts           # typed fetch wrappers
│       ├── api/stream.ts           # SSE consumer
│       ├── components/
│       │   ├── ChatPane.tsx        # Virtuoso list of historical msgs
│       │   ├── MessageBubble.tsx   # memoized; markdown + LaTeX + code
│       │   ├── StreamingBubble.tsx # separate component, only re-renders during stream
│       │   ├── InputBar.tsx        # textarea + send + model dropdown
│       │   ├── ModelPicker.tsx
│       │   ├── Sidebar.tsx         # conversation list
│       │   ├── SettingsPanel.tsx   # endpoint, save dir, theme
│       │   └── ui/                 # Glass, Button, Toast, etc.
│       ├── markdown/
│       │   ├── MarkdownRenderer.tsx # react-markdown wrapped + memoized
│       │   └── CodeBlock.tsx        # lazy hljs with IntersectionObserver
│       └── styles/globals.css
└── scripts/
    ├── dev.ps1                     # uv run + npm dev in parallel
    └── build.ps1                   # bundle frontend, then run backend
```

---

## 5. Implementation Phases

Each phase has a concrete **verify** check.

### Phase 1 — Project scaffold *(verify: `aoai-chat --help` runs)*
1.1 `uv init backend`, add deps: `fastapi sse-starlette uvicorn openai azure-identity pydantic-settings python-dotenv`.
1.2 `npm create vite@latest frontend -- --template react-ts`, add deps: `react-virtuoso react-markdown remark-gfm remark-math rehype-katex rehype-highlight zustand katex highlight.js @radix-ui/react-dropdown-menu @radix-ui/react-dialog clsx`.
1.3 Wire Tailwind v4, configure dark mode `class`, install Inter + JetBrains Mono via `@fontsource`.
1.4 Add `scripts/dev.ps1` (parallel `uv run uvicorn` + `npm dev`), Vite proxy `/api` → `127.0.0.1:8765`.

### Phase 2 — Backend: auth + config *(verify: `curl /api/config` returns persisted endpoint; token acquires successfully)*
2.1 `backend/app/auth.py`: factory `make_token_provider(scope="https://ai.azure.com/.default")` returning a callable per OpenAI SDK contract. Also expose `cognitiveservices.azure.com/.default` as fallback (user's sample script uses this; both produce valid AOAI tokens — Responses-API doc updated to `ai.azure.com`).
2.2 `backend/app/settings.py`: pydantic model `AppConfig { endpoint, default_deployment, save_dir, api_version, theme }`, persisted to `~/.aoai-chat/config.json`. Env-var override via `pydantic-settings`.
2.3 `routes/config.py`: `GET /api/config`, `PUT /api/config` (validates endpoint URL, creates save_dir if missing).

### Phase 3 — Backend: deployments listing *(verify: dropdown populates with real deployment names)*
3.1 `aoai_client.py::list_deployments()`: call `GET {endpoint}/openai/deployments?api-version=2024-10-21` with bearer token; parse `data[].id` + `data[].model.name`.
3.2 `routes/deployments.py`: `GET /api/deployments` → `[{ id, model, capabilities }]`. Capabilities detected by lookup table:
   ```python
   KNOWN_RESPONSES_MODELS = {"gpt-5.4-pro", "gpt-5.5"}   # explicit support
   KNOWN_RESPONSES_PREFIXES = ("gpt-5", "o1", "o3", "o4")  # heuristic fallback
   ```
   A deployment's `model.name` is checked against the set first, then the prefix list. Anything else gets `supports_responses_api=false` and uses Chat Completions.
3.3 Cache 60 s in-memory.

### Phase 4 — Backend: chat streaming *(verify: `curl -N /api/chat -d '...'` emits SSE deltas)*
4.1 `aoai_client.py::stream_response(...)`:
   - Build `OpenAI(base_url=f"{endpoint}/openai/v1/", api_key=token_provider)`.
   - If model supports Responses API: `client.responses.create(model=..., input=..., previous_response_id=..., stream=True, context_management={"type":"compaction","compact_threshold":200000}, store=True)`.
   - Else: fall back to `client.chat.completions.create(stream=True)` with full local history.
   - Iterate events; for Responses, forward `response.output_text.delta`, `response.created` (capture id), `response.completed` (capture usage). For Chat, forward `choices[0].delta.content`.
4.2 `routes/chat.py`: `POST /api/chat { conversation_id?, deployment, content }` → SSE stream of `{type, data}` events: `start` (with response_id), `delta`, `done` (with usage, latest response_id), `error`.
4.3 On `done`, append turn to transcript file (Phase 5).
4.4 Error event on AOAI 429/500 — frontend shows toast.

### Phase 5 — Backend: transcript persistence *(verify: completing a turn writes/updates a `.md` file)*
5.1 `transcript.py::write_turn(conversation_id, user_text, assistant_text, meta)`:
   - `save_dir` defaults to `<repo>/conversations/` (override via `AOAI_SAVE_DIR` env or settings).
   - File path: `{save_dir}/{date}-{slug}.md`, slug = first 6 words of first user message, kebab-cased.
   - On first turn: write YAML frontmatter `id, created, endpoint, deployment, response_id, total_tokens`.
   - Then append `## User\n\n{text}\n\n## Assistant\n\n{text}\n\n---\n`.
   - On subsequent turns: update frontmatter (`response_id`, `total_tokens`) in place, append new block.
5.2 `routes/conversations.py`:
   - `GET /api/conversations` — scan `save_dir`, parse frontmatter, return list sorted by mtime.
   - `GET /api/conversations/{id}` — parse a single file into structured turns.
   - `DELETE /api/conversations/{id}`.

### Phase 6 — Frontend: chat foundation *(verify: send message, see streamed reply, transcript visible on disk)*
6.1 `store/chatStore.ts` (Zustand): `{ conversations[], activeId, messages[], streaming: {content, responseId} | null, config }`.
6.2 `api/stream.ts`: thin SSE consumer using native `EventSource` (or `fetch` + `ReadableStream` if we need POST body — required, so use fetch+SSE manual parse).
6.3 `App.tsx` shell: Sidebar (left, conversations), ChatPane (center), InputBar (bottom).
6.4 Send flow: append user message → POST `/api/chat` → on `delta` events, update `streaming.content` → on `done`, commit streaming bubble to `messages[]` and clear `streaming`.

### Phase 7 — Frontend: rich rendering *(verify: equations render, code highlights, no XSS)*
7.1 `MarkdownRenderer.tsx`: `<ReactMarkdown remarkPlugins={[remarkGfm, remarkMath]} rehypePlugins={[rehypeKatex, rehypeHighlight]} components={{ code: CodeBlock }} />`.
7.2 KaTeX CSS imported once in `globals.css`. Verify `$E=mc^2$` and `$$\int_0^\infty e^{-x^2}dx = \sqrt{\pi}/2$$`.
7.3 `CodeBlock.tsx`: lazy-load language packs (`highlight.js/lib/languages/...`) via dynamic import, defer until in viewport via IntersectionObserver.
7.4 Wrap renderer in `React.memo` keyed by message id + content hash.

### Phase 8 — Frontend: 1M-context performance *(verify: 1000-message conversation scrolls at 60fps; streaming a new token doesn't re-render historical bubbles — measured via React DevTools profiler)*
8.1 `ChatPane.tsx`: `<Virtuoso data={messages} itemContent={...} followOutput="smooth" />` — only renders ~20 visible bubbles regardless of total count.
8.2 `StreamingBubble.tsx`: rendered **outside** the Virtuoso list, pinned at bottom. Subscribes only to `streaming.content`. When `streaming` becomes null, the committed message appears at the end of Virtuoso's `data` (zero re-render cost for historical items).
8.3 `MessageBubble.tsx`: `React.memo((prev, next) => prev.msg.id === next.msg.id && prev.msg.content === next.msg.content)`.
8.4 Markdown parse result cached per `(id, contentLength)` via `useMemo`. For >50KB messages, parse off the main thread via a lightweight Web Worker (`comlink`).
8.5 Server-side compaction: pass `context_management.compact_threshold = 200_000` so the wire payload stays bounded even at 1M tokens. UI only needs to render text the user actually scrolls to.

### Phase 9 — Future-like UI polish *(verify: design review against frontend-design skill checklist)*
9.1 Invoke the `frontend-design` skill at this phase boundary for the final visual pass.
9.2 Design tokens:
   - Background: deep `#0A0B14` with animated aurora gradient (cyan → violet, very low opacity, slow drift).
   - Surface: `rgba(255,255,255,0.04)` with `backdrop-blur-xl`, 1px `rgba(255,255,255,0.08)` border.
   - Accent: `#5EEAD4` (cyan-300) for primary actions, `#A78BFA` for user-message highlight.
   - Typography: Inter for UI, JetBrains Mono for code; tight `tracking-tight` on headings.
9.3 Micro-interactions: spring animations on send (Framer Motion), subtle "typing" pulse on streaming bubble, glow on input focus, command-K palette for conversation switch.
9.4 Empty state: animated starfield + suggestion chips.
9.5 Accessibility: focus rings, ARIA roles on Virtuoso, prefers-reduced-motion fallback.

### Phase 10 — Tests & docs *(verify: `uv run pytest` passes; `npm test` passes; README has 60-second quickstart)*
10.1 Backend: pytest for `transcript.py` round-trip, `aoai_client` mocked w/ recorded SSE fixtures, `routes/config` validation.
10.2 Frontend: Vitest for store reducers, RTL for `MarkdownRenderer` math/code rendering, Playwright smoke for send-receive-persist flow against a mock backend.
10.3 README: prerequisites (`az login`), quickstart, screenshot, troubleshooting (most common: 401 → run `az login`; deployments empty → check resource role assignment).

---

## 6. Acceptance Criteria (testable, per requirement)

| Req | Criterion | How to verify |
|---|---|---|
| R1 | App authenticates with zero secrets in source. After `az login`, app starts and successfully calls `/openai/deployments`. | `grep -r "api_key\|api-key" backend/app` returns only `api_key=token_provider` (the callable). `Get-Content ~/.aoai-chat/config.json` shows no key. |
| R2 | Setting endpoint to `https://aoai-l-eastus2.openai.azure.com/` then opening Settings shows a populated deployment dropdown including `gpt-5.4-pro` (if present in the resource). | Manual click-through; `curl /api/deployments` returns ≥1 item. |
| R3 | For Responses-capable model, request hits `/openai/v1/responses` (verify via FastAPI debug log) and consecutive turns include `previous_response_id`. For non-capable model, hits `/openai/v1/chat/completions`. | Backend log assertion; integration test with mocked SDK. |
| R4 | After a 3-turn conversation, file `~/.aoai-chat/conversations/{date}-{slug}.md` exists, opens cleanly in any Markdown viewer, contains all turns and frontmatter. | `Test-Path` + content match. |
| R5 | A message containing `Recall $e^{i\pi}+1=0$ and $$\sum_{n=1}^\infty 1/n^2 = \pi^2/6$$` renders as proper math, not as raw `$…$`. | RTL test asserts presence of `.katex` DOM nodes. |
| R6 | Visual review: glass surfaces, dark theme, smooth animations (no jank at 60fps), accent colors used consistently, all interactive elements have focus styles. | Manual + Lighthouse a11y ≥ 95. |
| R7 | Load a fixture conversation with 1,000 messages averaging 2KB each (~2MB DOM text); scroll smoothly; send a new message — historical bubbles **do not re-render** (verified in React DevTools Profiler: only StreamingBubble re-renders during streaming). | Profiler screenshot in PR; FPS measured via Performance panel. |

---

## 7. Risks & Mitigations

| # | Risk | Likelihood | Mitigation |
|---|---|---|---|
| K1 | `gpt-5.4-pro` / `gpt-5.5` deployment may not exist in the user's resource yet, or may be named differently in their resource. | Med | Confirmed supported: `KNOWN_RESPONSES_MODELS = {"gpt-5.4-pro","gpt-5.5"}` in code. App still lists *all* deployments from the endpoint; if a model is missing from the known set, falls back to Chat Completions and shows a toast. On Responses-API 404, hot-fallback to `chat.completions.create` with full local history for that turn. |
| K2 | Token scope mismatch: user's sample script uses `cognitiveservices.azure.com/.default`; current Responses-API docs use `ai.azure.com/.default`. | Med | Try `ai.azure.com/.default` first; on 401, retry once with `cognitiveservices.azure.com/.default`. Log which scope succeeded. |
| K3 | `azure-identity` requires `az login` on a fresh machine — confusing error otherwise. | High | Catch `CredentialUnavailableError` → return 401 with body `{ "error": "az_login_required", "hint": "Run `az login` then refresh." }`; frontend shows actionable banner. |
| K4 | 1M-token DOM still slows down even with virtualization if a single message is huge (e.g. 200K-token assistant reply). | Med | (a) Cap single-bubble height with "Show more" expander > 8000 chars. (b) For huge replies, render in Web Worker. (c) Test with worst-case fixture in Phase 8. |
| K5 | SSE through corporate proxies sometimes buffers; user sees no streaming. | Low | Set `X-Accel-Buffering: no` and `Cache-Control: no-cache` headers; flush after each event. Document workaround. |
| K6 | Markdown writer corrupts file on concurrent writes (rare — single user but could mid-turn-cancel). | Low | Atomic write: serialize whole file to `*.tmp`, `os.replace()` to final name. |
| K7 | `previous_response_id` chaining: if server-stored response expires (30-day default), next turn 404s. | Low | Detect 404 on chain, replay full local history once to recreate state. |
| K8 | Frontend bundle bloat from highlight.js (all languages = ~600KB). | Med | Dynamic import per language, see Phase 7.3. Default bundle includes only 6 common langs. |

---

## 8. Verification Steps (end-to-end)

After every phase, run:
```powershell
# Backend
cd backend; uv run pytest -q; uv run ruff check app

# Frontend
cd frontend; npm typecheck; npm test --run; npm lint
```

Pre-release smoke (run after Phase 10):
1. `scripts/dev.ps1` → browser opens to `http://localhost:8765`.
2. Open Settings → paste endpoint `https://aoai-l-eastus2.openai.azure.com/` → Save.
3. Deployment dropdown populates within 2s.
4. Send "Solve $\int_0^1 x^2 dx$ and show working" → tokens stream, final reply contains rendered math.
5. Refresh page → conversation persists in sidebar.
6. Open `~/.aoai-chat/conversations/*.md` in VS Code → readable Markdown with frontmatter.
7. Run perf script that injects 1000-message fixture → scroll FPS ≥ 55, profile shows only StreamingBubble re-rendering.

---

## 9. Open Items Surfaced by User Sample

The user shared a Chat-Completions-style script:
```python
client = AzureOpenAI(azure_endpoint=..., azure_ad_token_provider=..., api_version="2025-01-01-preview")
client.chat.completions.create(model=deployment, messages=messages, max_completion_tokens=6553, stream=False)
```

Differences we'll introduce in the app:
- **Switch client class**: `OpenAI(base_url=f"{endpoint}/openai/v1/", api_key=token_provider)` — the v1 pattern recommended for Responses API. (Keep `AzureOpenAI` legacy class as the Chat-Completions fallback path.)
- **Switch API surface**: `client.responses.create(input=..., stream=True, previous_response_id=...)` instead of `client.chat.completions.create(messages=..., stream=False)`.
- **Switch token scope**: `https://ai.azure.com/.default` (with `cognitiveservices.azure.com/.default` fallback — both valid; the user's sample used the latter).
- **Enable streaming**: `stream=True` always; backend forwards as SSE.
- **Enable server-side compaction**: critical for the 1M-context requirement.

---

## 10. Effort Estimate

| Phase | Est. time | Notes |
|---|---|---|
| 1 — Scaffold | 0.5 d | |
| 2 — Auth + config | 0.5 d | |
| 3 — Deployments | 0.25 d | |
| 4 — Chat streaming | 1.0 d | Responses + Chat dual paths |
| 5 — Transcript | 0.5 d | |
| 6 — Frontend foundation | 1.0 d | |
| 7 — Markdown/LaTeX/code | 0.75 d | |
| 8 — Virtualization & perf | 1.0 d | Includes profiling + fixture |
| 9 — UI polish | 1.0 d | `frontend-design` skill pass |
| 10 — Tests + docs | 0.75 d | |
| **Total** | **~7 days** | Single dev, MVP-shippable |

---

## Post-v1 reliability + UX iterations

Tracked from the long-running troubleshooting/polish session of 2026-05:

### Parameters panel
- System-prompt presets (Default, Senior software engineer, Legal analyst, Math researcher, AI/ML researcher, Data analyst, Writing editor, Socratic tutor)
- Per-model reasoning-effort options (gpt-5.4-pro is restricted to `medium / high / xhigh`)
- Max-output-tokens slider, range 256 – 131,072, step 1,024, default 32,768 (raised from initial 16,384)
- **xhigh warning card** appears under the effort control — explains that xhigh frequently consumes the entire output-token budget on internal reasoning

### Streaming bubble
- Elapsed timer (Clock icon + smart formatting up to hours)
- **Freshness indicator** with color-coded "last update Xs ago" — green pulse <5s, cyan <30s, amber <90s, rose >90s
- **Stop button** — module-scoped `AbortController` + AbortError distinction in catch
- Reasoning summary live-streaming as a ChatGPT-style compact ticker (last ~500 chars only, top edge masked)
- **Reasoning-tail-only memory** — `streaming.reasoning` capped at 1500 chars in store; `reasoningCharsTotal` counter tracks total separately. Prevents 60k+ char strings from bloating React state on long reasoning sessions
- rAF-batched streaming updates — multiple deltas per frame coalesce into a single React render

### Reliability watchdogs
- httpx read timeout: 30 min (env-configurable via `AOAI_READ_TIMEOUT`)
- httpx max_retries=0 (prevents silent re-billing of partial streams)
- Stale-stream watchdog: 5 min between any SSE events → auto-abort
- **No-content watchdog**: 20 min total elapsed with zero `output_text.delta` → auto-abort with "try lower effort" hint
- Distinct catch-block branches for stale/no-content/user-abort/real error

### Error UX
- **Inline error card** (`<InlineError />`) replaces toast for stream failures — persistent, includes Retry button and Dismiss button
- Retry path uses `sendMessage(content, { skipUserMessage: true })` so the original user message isn't duplicated in `activeMessages`
- Error message includes the specific char count of reasoning when budget exhaustion is detected

### Terminal-style history
- `↑` / `↓` arrows in the input bar navigate previous prompts (caret-position-aware for multi-line)
- Persisted in `localStorage` (`aoai-chat:prompt-history`, capped at 200, dedupes consecutive duplicates)
- `history · N / M` indicator chip when browsing

### Visual identity (iOS 26 Liquid Glass)
- **Onest** variable font replaces Inter as primary sans
- Fraunces italic scoped to pondering math phrases only
- Pure `#000000` background with very faint corner-only nebula gradient
- `.glass` utility — backdrop-filter: blur(22px) saturate(160%), top-edge specular highlight via `::before`, multi-layer shadow, brighter top border
- `.glass-tint-user` (violet wash), `.glass-tint-assistant` (neutral), `.glass-input`, `.glass-strong` variants
- Applied to: example chips, message bubbles, streaming bubble, input bar
- Empty state: "what would you like to build?" — Onest medium 56px, sky-300 accent on "build", clickable example chips for Math/Reasoning/Code/Writing

### Performance trims
- Removed three giant `blur-[140px]` aurora layers (most expensive paint cost)
- Consolidated 6 background decoration layers → 3 (single starfield, single galaxy, lead-stars)
- Dropped all background-position animations (was forcing fullscreen repaint per frame)
- Removed SVG noise overlay (mix-blend-mode was expensive)
- Reasoning text capped at 1.5KB in memory (was unbounded)

### Backend hardening
- `NoCacheHTMLMiddleware` — sends `Cache-Control: no-cache, no-store, must-revalidate` on HTML responses to prevent stale-bundle-after-deploy issues
- `index.html` meta tags as belt-and-suspenders for reverse-proxy caching
- Handles `response.failed`, `response.incomplete`, and non-success terminal statuses in `_stream_responses`, surfacing them as `error` SSE events

### Documented model knowledge
- gpt-5.4-pro: Responses API only (no Chat Completions). Supports `medium / high / xhigh`. 1,050,000 context, 128,000 max output (combined reasoning + text).
- gpt-5.5: Same context and output limits. Supports full effort range.
- Token budget conflation — `max_output_tokens` caps `reasoning_tokens + output_tokens`. The API does NOT reject values above 128,000; it silently clamps. Slider max is 131,072 to give a small headroom marker.
- xhigh failure mode is reasoning-budget-exhaustion, not a bug. The fix is either lower effort or higher max_output_tokens.

---

## Implementation Deltas (vs original plan)

Things we discovered or changed while building:

1. **`/openai/deployments` doesn't exist** on this resource (404 on all api-versions). The data plane has `/openai/models` but it returns the *region's model catalog* (352 versioned SKUs), not the user's actual *deployments* — those live in the control plane (ARM). **Fix**: user manages the deployment list in `.aoai-chat/config.json`; defaults seed `gpt-5.4-pro` and `gpt-5.5`. `POST /api/deployments` and `DELETE /api/deployments/{name}` mutate it.

2. **`gpt-5.4-pro` is Responses-only** — confirmed via probe: `/openai/deployments/gpt-5.4-pro/chat/completions` returns `400 unsupported`. So the Chat-Completions fallback path won't help that model; we rely on the `KNOWN_RESPONSES_MODELS` set to route it correctly.

3. **OpenAI v1 unified path > AzureOpenAI client** — we use `AsyncOpenAI(base_url=".../openai/v1/", api_key=<pre-fetched token string>)` rather than `AsyncAzureOpenAI`. Two reasons: (a) v1 is api-version-free, sidestepping the "Responses API requires api-version 2025-03-01-preview+" gate; (b) `AsyncOpenAI(api_key=<callable>)` awaits the callable's return value, which fails for the sync `azure-identity` provider with `object str can't be used in 'await' expression`. Pre-fetching the token (one sync call per request) is fine — AAD tokens last ~60 min and we create a fresh client per chat request.

4. **`context_management` is a list, not an object** — the API expects `[{"type":"compaction","compact_threshold":200000}]`, not the dict shape the docs originally suggested.

5. **`npm` instead of `pnpm`** — pnpm wasn't installed on the machine; npm 11 works fine for our scale.

6. **`AsyncOpenAI` callable api-key incompat** — documented in code comments so future maintainers don't switch back.

7. **Streaming bubble inside Virtuoso, not outside** — putting the streaming row in the virtualized list (rather than as a sibling) gives in-place visual context; React.memo + key-by-id ensures historical bubbles don't re-render. During streaming we render plain text (no markdown re-parse per token); the formatted markdown view kicks in once the message commits.

8. **5-way bundle split** — `react` / `markdown` / `katex` / `hljs` / `ui` chunks. Initial paint only needs `index` (78 KB) + `ui` (220 KB). KaTeX (258 KB) and markdown (353 KB) load on demand via the React component graph; cached aggressively after first load.

---

## 11. Decisions Locked In

User confirmed in chat:

1. ✅ **Backend = Python** (FastAPI + `openai` SDK v1 + `azure-identity`).
2. ✅ **Single-user, local-only** — no auth between browser and backend.
3. ✅ **Transcripts saved inside the repo** at `<repo>/conversations/*.md`. Config at `<repo>/.aoai-chat/config.json` (hidden folder, gitignored).
4. ✅ **Hardcoded support for `gpt-5.4-pro` and `gpt-5.5`** as Responses-API models. Other deployments still listed and routed to Chat Completions automatically.

Inherited defaults (still flagged for the user to redirect if needed):

5. **Distribution** — `uv` + `npm` dev scripts; single `aoai-chat` entry point. No packaging in v1.
6. **Theme** — dark by default; light-mode toggle deferred to v2.
7. **Port** — `localhost:8765` (configurable via `AOAI_PORT` env).
