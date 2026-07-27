# Resolved Bugs and Reliability Improvements

This document records the defects addressed in the GPT-5.6, streaming
reliability, and UX stabilization work.

## Summary

| Area | Symptom | Root cause | Resolution |
|---|---|---|---|
| Rendering performance | Typing, panels, and streamed responses felt globally laggy. | Large and frequently changing surfaces used `backdrop-filter`, forcing expensive compositing and repainting. | Replaced live backdrop sampling with static layered gradients, borders, highlights, and shadows. |
| Conversation scrolling | Reaching the end of a long conversation could jump the viewport thousands of pixels upward. | React Virtuoso reapplied delayed initial positioning after variable-height Markdown had been measured. | Replaced the conflicting virtual scroll owner with native scrolling; pin on conversation load or an explicit send, and preserve upward reading position through assistant completion and later resizes. |
| Background streaming | High-effort requests could end with an empty-response card even though Azure continued processing. | Azure background + streaming can terminate SSE before a terminal response event. | Poll the stored response after premature stream termination and deliver the completed output. |
| Missing streamed text | A response could complete with text in the final response object but no preceding text deltas. | The client relied exclusively on `response.output_text.delta` events. | Reconcile streamed text with final `output_text` and emit only the missing suffix. |
| Stream startup errors | Failure before the first SSE event escaped as an unstructured server error. | First-event acquisition happened outside the guarded stream iteration. | Convert first-event failures into structured SSE errors. |
| Long-running authentication | Background polling could outlive the bearer token used to create the request. | The OpenAI client received a one-time token string. | Upgraded the OpenAI SDK and supplied an async refreshing Azure token provider for every HTTP request. |
| Recovery resilience | One network, timeout, throttling, or transient Azure error abandoned recovery. | Retrieval had no bounded transient retry policy. | Retry connection errors, timeouts, HTTP 408/409/429, and 5xx responses up to four attempts; honor numeric `Retry-After` values up to 60 seconds; cancel on exhaustion. |
| Stop button billing | Stop closed the browser stream but Azure could continue consuming tokens. | There was no remote cancellation API. | Added an idempotent cancellation endpoint and call it from Stop and watchdog paths. |
| Early cancellation | Stop or disconnect before `response.created` could lose the response ID and orphan the Azure job. | Cancellation raced Azure ID delivery. | Shield creation and first-event acquisition for up to 60 seconds to obtain and cancel the response ID; cleanup remains best-effort if Azure never supplies an ID. |
| Framework cancellation | AnyIO could cancel cleanup while FastAPI was closing SSE. | Cleanup awaited Azure inside the already-cancelled task scope. | Shield generator closure, response-ID acquisition, and Azure cancellation from the outer AnyIO cancellation scope. |
| Stale streaming UI | A queued animation frame could resurrect a stopped or failed stream. | Pending rAF state was cleared only on successful completion. | Cancel the queued frame and clear pending state in every exit path. |
| Conversation races | Starting or switching chats during generation could mix stream state between conversations. | Navigation cleared shared streaming state while the old request was still active. | Block new, select, delete, and send actions until the active generation is stopped. |
| Chat fallback Stop | Stop could wait 30 seconds after Responses fell back to Chat Completions. | The request remained marked as background-cancellable despite having no response ID. | Downgrade cancellability on fallback or a Chat start event and abort locally immediately. |
| GPT-5.6 support | The UI could not select `max` effort or Pro mode and duplicated model rules. | Capability knowledge was hardcoded separately in frontend and backend. | Added one backend-owned static capability map and separate effort/mode controls driven by the API contract. |
| Output limits | The previous slider exceeded the documented 128,000-token maximum. | The UI used a generic 131,072 upper bound. | Clamp requests and controls to each deployment's documented limit in the backend capability map. |
| Parameter persistence | Persisted Pro mode reset to Standard during application startup. | Validation ran before deployment metadata had loaded. | Defer model-specific coercion until the selected deployment is available. |

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
uv run ruff check app/aoai_client.py app/routes/chat.py app/routes/deployments.py app/schemas.py app/settings.py tests/test_aoai_client.py

cd ../frontend
npm run build
```

Observed results:

- Backend suite: 56 tests passing.
- Changed backend files pass Ruff.
- Frontend TypeScript and production build pass.
- Live Azure calls passed for Standard + `none`, Standard + `max`,
  Pro + `medium`, and Pro + `max`.
- Live browser Stop returned Azure status `cancelled`.
- Scroll regression improved from jumps of up to 16,822px to 0px.
- Upward user scroll remained unchanged during content resize and assistant
  completion.
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
