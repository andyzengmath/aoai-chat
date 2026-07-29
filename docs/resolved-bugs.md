# Resolved Bugs and Reliability Improvements

This document records the defects addressed in the GPT-5.6, streaming
reliability, and UX stabilization work.

## Summary

| Area | Symptom | Root cause | Resolution |
|---|---|---|---|
| Rendering performance | Typing, panels, and streamed responses felt globally laggy. | Large and frequently changing surfaces used `backdrop-filter`, forcing expensive compositing and repainting. | Replaced live backdrop sampling with static layered gradients, borders, highlights, and shadows. |
| Conversation scrolling | Reaching the end of a long conversation could jump the viewport thousands of pixels upward. | React Virtuoso reapplied delayed initial positioning after variable-height Markdown had been measured. | Replaced the conflicting virtual scroll owner with native scrolling; pin on conversation load or an explicit send, and preserve upward reading position through assistant completion and later resizes. |
| Long-chat rendering | Scrolling a long math conversation degraded to 50ms frame p95 with frequent long tasks. | Every offscreen Markdown and KaTeX subtree remained eligible for style, layout, and paint work (146K DOM nodes in the measured chat). | Keep messages mounted for accessibility while applying `content-visibility: auto` and role-specific intrinsic sizes to skip offscreen rendering work. |
| Background streaming | High-effort requests could end with an empty-response card even though Azure continued processing. | Azure background + streaming can terminate SSE before a terminal response event. | Poll the stored response after premature stream termination and deliver the completed output. |
| Missing streamed text | A response could complete with text in the final response object but no preceding text deltas. | The client relied exclusively on `response.output_text.delta` events. | Reconcile streamed text with final `output_text` and emit only the missing suffix. |
| Output-budget truncation | `max_output_tokens` discarded useful partial output and forced the user to restart the expensive round. | Incomplete responses were treated as generic errors and their response IDs were not retained. | Persist the partial turn, usage, status, reason, deployment, and response ID; restore the Continue action after reload and offer a fresh chained budget. |
| Terminal persistence ordering | A client could receive `done` or `incomplete` and still lose the turn after disconnecting. | Terminal SSE events were emitted before the transcript's atomic write. | Buffer terminal events, persist first, and only then deliver the terminal and saved events. |
| Refusal handling | Valid model refusals appeared as empty-response failures. | `Response.output_text` excludes refusal blocks and refusal events were ignored. | Stream and reconcile refusal text as visible assistant output. |
| SDK error events | Stream errors lost their Azure code and message. | The app read a nonexistent nested `event.error` object in SDK 2.48. | Read the SDK's direct `event.code` and `event.message` fields. |
| Stream startup errors | Failure before the first SSE event escaped as an unstructured server error. | First-event acquisition happened outside the guarded stream iteration. | Convert first-event failures into structured SSE errors. |
| Long-running authentication | Background polling could outlive the bearer token used to create the request. | The OpenAI client received a one-time token string. | Upgraded the OpenAI SDK and supplied an async refreshing Azure token provider for every HTTP request. |
| Recovery resilience | One network, timeout, throttling, or transient Azure error abandoned recovery. | Retrieval had no bounded transient retry policy. | Retry connection errors, timeouts, HTTP 408/409/429, and 5xx responses up to four attempts; honor numeric `Retry-After` values up to 60 seconds; cancel on exhaustion. |
| Repeated Azure 500 errors | Terminal background jobs failed with `server_error`, zero output, and no usage, forcing manual retries. | The app surfaced every terminal failure even when Azure had conclusively recorded that no work/output survived. | Retry only confirmed terminal zero-output/no-usage `server_error` jobs twice (2s and 5s), visibly within the same SSE request; never retry ambiguous create failures or failures after visible output. |
| Stop button billing | Stop closed the browser stream but Azure could continue consuming tokens. | There was no remote cancellation API. | Added remote cancellation for Stop and watchdog paths, authorized by an opaque per-stream capability token. |
| Early cancellation | Stop or disconnect before `response.created` could lose the response ID and orphan the Azure job. | Cancellation raced Azure ID delivery. | Shield creation and first-event acquisition for up to 60 seconds to obtain and cancel the response ID; cleanup remains best-effort if Azure never supplies an ID. |
| Framework cancellation | AnyIO could cancel cleanup while FastAPI was closing SSE. | Cleanup awaited Azure inside the already-cancelled task scope. | Shield generator closure, response-ID acquisition, and Azure cancellation from the outer AnyIO cancellation scope. |
| Stale streaming UI | A queued animation frame could resurrect a stopped or failed stream. | Pending rAF state was cleared only on successful completion. | Cancel the queued frame and clear pending state in every exit path. |
| Conversation races | Starting or switching chats during generation, or completing an older load after a newer selection, could activate the wrong conversation. | Navigation and asynchronous conversation reads updated shared state without ownership sequencing. | Block navigation during generation and use a load generation token so stale reads cannot overwrite newer state. |
| Stop rollback | Stopping before a terminal response left an unsaved user prompt and sometimes a nonexistent conversation ID on screen. | Optimistic state was never reverted on user abort. | Restore the pre-send conversation state unless a durable terminal event was received. |
| Chat fallback Stop | Stop could wait 30 seconds after Responses fell back to Chat Completions. | The request remained marked as background-cancellable despite having no response ID. | Downgrade cancellability on fallback or a Chat start event and abort locally immediately. |
| GPT-5.6 support | The UI could not select `max` effort or Pro mode and duplicated model rules. | Capability knowledge was hardcoded separately in frontend and backend. | Added one backend-owned static capability map and separate effort/mode controls driven by the API contract. |
| Output limits | The previous slider exceeded the documented 128,000-token maximum. | The UI used a generic 131,072 upper bound. | Clamp requests and controls to each deployment's documented limit in the backend capability map. |
| Misleading max configuration | Pro + `max` could still run with the 32,768-token default output budget. | Reasoning effort and output budget are independent API controls. | Show the active budget constraint and provide a one-click **Use 128K** action without silently overriding explicit cost controls. |
| Parameter persistence | Persisted Pro mode reset to Standard during application startup. | Validation ran before deployment metadata had loaded. | Defer model-specific coercion until the selected deployment is available. |
| Deployment discovery | An environment-selected custom deployment was active but absent from the picker. | Environment overrides changed the default without joining the known-deployment list. | Merge the effective default into the returned deployment registry. |
| Unverified model controls | Unknown GPT-5/o-series deployments advertised effort levels and a 128K output cap without a verified contract. | A generic Responses fallback supplied optimistic capabilities. | Keep Responses routing support while publishing conservative empty capabilities for unknown deployments. |
| Configured token destinations | A mutable endpoint or token scope could send Azure bearer tokens and prompts outside Azure AI. | Configuration accepted HTTP, arbitrary hosts, and arbitrary audiences. | Require HTTPS, official Azure AI host suffixes, port 443, and cloud-matched public, Government, or China token scopes and Entra authorities. |

## GPT-5.6 Contract

The configured Azure deployment was queried through the Azure management
plane on 2026-07-25:

- Deployment: `gpt-5.6-sol`
- Model version: `2026-07-09`
- SKU: `GlobalStandard`
- Context window: 1,050,000 tokens
- Maximum input: 922,000 tokens
- Maximum output: 128,000 tokens
- Efforts: `none`, `low`, `medium`, `high`, `xhigh`, `max`
- Modes: `standard`, `pro`

Pro is a reasoning mode on the same deployment, not a separate
`gpt-5.6-sol-pro` model. Mode and effort are independent and can be combined.
The application does not query ARM at runtime; it exposes a static,
backend-owned capability map based on:

- [Microsoft's Azure OpenAI model table](https://learn.microsoft.com/azure/foundry/openai/how-to/reasoning)
  (updated 2026-07-23 when verified)
- [OpenAI's reasoning guide](https://developers.openai.com/api/docs/guides/reasoning)
  (accessed 2026-07-25)

## Verification Evidence

Verification was performed on Windows with Chromium 145 at a 1440×900
viewport on 2026-07-27.

Repeatable repository checks:

```powershell
cd backend
uv run pytest -q
uv run ruff check app/aoai_client.py app/auth.py app/routes/chat.py app/routes/config.py app/routes/conversations.py app/routes/deployments.py app/schemas.py app/settings.py app/transcript.py tests/test_aoai_client.py tests/test_transcript.py

cd ../frontend
npm run build
```

Observed results:

- Backend suite: 99 tests passing.
- Changed backend files pass Ruff.
- Frontend TypeScript and production build pass.
- Live Azure calls passed for Standard + `none`, Standard + `max`,
  Pro + `medium`, and Pro + `max`.
- Live browser Stop returned Azure status `cancelled`.
- A live forced-incomplete Azure response was saved and continued through
  `previous_response_id` to a completed response without repeating text.
- Scroll regression improved from jumps of up to 16,822px to 0px.
- Upward user scroll remained unchanged during content resize and assistant
  completion.
- Long-chat scroll frame p95 improved from 50ms to 16.8ms; repeated isolated
  runs kept slow frames between 3.3% and 4.5%.
- Performance figures are medians/percentiles from three randomized local
  browser trials:
  - streamed frame p95: 16.8ms
  - slow streamed frames: 0.26%
  - typing latency p95: 31.6ms
  - panel frame p95: 16.7ms

## Local Runtime Remediation

The local service was also moved from an assistant-owned process tree to a
Windows Task Scheduler process after detached shells were terminated
externally. The scheduled launcher is a workstation-level remediation and is
not installed by this pull request.
