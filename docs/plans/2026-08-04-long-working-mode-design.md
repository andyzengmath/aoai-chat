# Long Working Mode Design

Status: Approved, architecture-reviewed  
Date: 2026-08-04  
Target version: 0.2.0

## Purpose

Long Working Mode runs a bounded, durable solver-verifier loop for difficult
mathematical problems. It continues after browser disconnects, safely recovers
from service restarts, exposes each round for inspection, and publishes a final
answer only after Standard verification and a fresh Pro confirmation.

The feature improves persistence and scrutiny; it does not guarantee that a
proof is mathematically correct. The UI must describe successful results as
model-verified, never certified or formally proven.

## Goals

- Alternate a Pro solver and Standard+max verifier every round.
- Require a fresh, unchained Pro+max confirmation after tentative acceptance.
- Continue server-side when the browser disconnects.
- Enforce round admission and active-time deadlines.
- Stop after a soft cumulative-token threshold, allowing at most one-call
  overshoot.
- Persist execution, call, event, and publication state transactionally.
- Recover known Azure jobs without duplicate submission.
- Show collapsed solver/verifier round cards and the exact accepted proof.
- Preserve normal chat and existing transcript behavior additively.

## Non-goals

- Formal proof checking or theorem-prover integration in version one.
- Multiple simultaneous Long Working runs.
- Running the loop in the browser.
- Rewriting an accepted proof after verification.
- Automatically resubmitting an Azure request whose acceptance is ambiguous.
- Supporting deployments without live-verified Responses API, structured
  output, Pro mode, and max-effort capabilities.
- Guaranteeing a strict token ceiling; Azure reports exact usage after a call.

## Architecture

The FastAPI backend owns a durable orchestrator. Starting a run creates one
logical conversation turn and returns `202 Accepted`; the browser subscribes to
replayable events but does not own execution.

SQLite is authoritative for run metadata, Azure call state, events, ownership,
budgets, and publication. Large proof and critique bodies are immutable,
content-addressed UTF-8 files. Markdown transcripts are idempotent projections
for portability, not the transaction boundary.

### Agent topology

1. Solver chain
   - Uses the selected deployment.
   - Mode is always `pro`.
   - Effort is configurable and defaults to `max`.
   - Output budget is separately configurable.
   - Maintains its own `previous_response_id`.

2. Standard verifier chain
   - Uses the same deployment by default.
   - Mode is always `standard`.
   - Effort defaults to `max`.
   - Output budget is separately configurable.
   - Maintains a response chain independent of the solver.

3. Pro confirmation
   - Runs only after Standard verifier `accept`.
   - Uses `pro` mode and `max` effort.
   - Is a fresh, unchained response with no `previous_response_id`.
   - Receives only the original problem, exact candidate proof, candidate hash,
     and verifier rubric. It does not receive the Standard verdict or critique.

The solver receives the latest verifier critique in its next input. The
Standard verifier receives the original problem plus a conditional review
target:

- candidate: exact candidate proof bytes/hash and canonical solver-result hash
- give-up: exact canonical solver-result JSON/hash with `candidate_sha256=null`

A Pro rejection becomes the next solver critique, but its response never enters
the Standard verifier chain.

## Agent Output Contracts

Agents return versioned strict structured outputs. Stored summaries are
explicit artifacts, not hidden chain-of-thought.

All Pydantic models use `extra="forbid"`. All JSON Schema properties are
required; nullable values use explicit `null`. Confidence is bounded to
`[0, 1]`. Server-side validators enforce conditional nonempty fields.

### Solver result

```json
{
  "schema_version": 1,
  "status": "candidate",
  "progress_summary": "What changed in this round",
  "candidate_proof": "A polished proof suitable for final publication",
  "unresolved_points": [],
  "give_up_reason": null,
  "confidence": 0.72
}
```

`status` is `candidate` or `give_up`. A candidate requires a nonempty proof.
Give-up requires a nonempty reason and enough summary for the verifier to judge
whether useful avenues remain.

### Verifier result

```json
{
  "schema_version": 1,
  "review_target": "candidate",
  "solver_artifact_sha256": "hex digest",
  "candidate_sha256": "hex digest or null",
  "verdict": "revise",
  "summary": "Overall assessment",
  "critique": "Concrete mathematical objections and repair direction",
  "checked_claims": [
    {
      "claim": "Claim being checked",
      "status": "failed",
      "rationale": "Why it fails"
    }
  ],
  "required_changes": ["Specific changes required"],
  "give_up_reason": null,
  "confidence": 0.91
}
```

`verdict` is `accept`, `revise`, or `give_up`; claim status is `passed`,
`failed`, or `uncertain`. `review_target` is `candidate` or `give_up`.

Every solver result, including give-up, is stored as canonical JSON and hashed.
Standard verification must echo `solver_artifact_sha256`. Candidate review also
requires the SHA-256 of the exact proof bytes. Pro confirmation is valid only
for a candidate and must echo the candidate hash. Completion requires Standard
and Pro acceptance of the same proof hash.

Give-up transitions are explicit:

- `candidate + accept` -> Pro confirmation
- `candidate + revise` or `candidate + give_up` -> next solver round
- `give_up + give_up` with matching solver artifact hash -> `gave_up`
- `give_up + revise` -> next solver round
- `give_up + accept` -> invalid structured result and `needs_attention`

Pro confirmation is forbidden when no candidate hash exists.

Structured outputs are mandatory. A live capability probe for the deployment
is required before enabling Long Working Mode. There is no permissive text
parser fallback. Refusal and incomplete terminal states are handled before
structured parsing.

## State Machines

### Run phases

```text
queued
  -> solver_planned
  -> solver_running
  -> verifier_planned
  -> verifier_running
  -> confirmation_planned
  -> confirmation_running
  -> accepted_unpublished
  -> published
  -> completed
```

Paused or terminal alternatives:

```text
gave_up
budget_exhausted
stop_requested
cancelling
stopped
cancel_unknown
failed
needs_attention
submission_unknown
abandoned
```

One round is one solver call followed by one Standard verifier call. Pro
confirmation does not increment the round number.

### Authoritative phase classification

`holds_global_slot` is the SQLite uniqueness predicate. The conversation lease
matches it except during the final projection transaction, where ownership is
released only after publication is durable.

| Phase | Status | Terminal | Resumable | Holds global slot | Holds conversation lease | Default actions |
|---|---|---:|---:|---:|---:|---|
| `queued` | queued | no | no | yes | yes | stop |
| `*_planned`, `*_running` | running | no | no | yes | yes | stop |
| `confirmation_*` | running | no | no | yes | yes | stop |
| `accepted_unpublished`, `published` | running | no | no | yes | yes | none |
| `budget_exhausted` | paused | no | yes after extension | yes | yes | extend_limits, abandon |
| `stop_requested`, `cancelling` | running | no | no | yes | yes | none |
| `stopped` | paused | no | yes | yes | yes | resume, abandon |
| `needs_attention` | paused | no | reason-dependent | yes | yes | reason-dependent, abandon |
| `submission_unknown` | paused | no | no | yes | yes | recheck, abandon, clone |
| `cancel_unknown` | paused | no | no | yes | yes | recheck, abandon |
| `completed` | terminal | yes | no | no | no | none |
| `gave_up` | terminal | yes | no | no | no | none |
| `failed` | terminal | yes | no | no | no | none |
| `abandoned` | terminal | yes | no | no | no | none |

For `budget_exhausted`, `resumable` becomes true and actions add `resume` only
after limits are increased enough to admit the next required call.
`needs_attention` exposes actions from its typed pause reason. Clone atomically
transfers both global and conversation ownership from `submission_unknown` to
the new run.

### Azure call ledger

Every Azure attempt has an immutable identity:

```text
(run_id, round_number, role, phase, attempt_number)
```

Each call transitions:

```text
planned -> submitting -> accepted -> terminal -> materialized
```

The ledger stores request hash, role configuration, predecessor response ID,
candidate hash where applicable, Azure response ID, usage, timestamps,
terminal status, and artifact hash.

A call in `submitting` without a response ID after restart becomes
`submission_unknown`. It is never automatically resubmitted. Available actions
are wait/check again, abandon the run, or explicitly clone from the last safe
checkpoint with a duplicate-cost warning.

### Round algorithm

1. Admit the solver only if round and active-time limits allow another call.
2. Persist the solver call as `planned`, then `submitting`.
3. Run the Pro solver; persist response ID immediately after
   `response.created`.
4. Materialize and validate the solver artifact.
5. Apply the soft observed-token threshold.
6. Run and materialize the Standard verifier using the canonical solver
   artifact hash and, for a candidate, the proof hash.
7. Apply its verdict:
   - `revise`: use the critique in the next solver round.
   - `give_up`: finish only if it reviewed and agreed with a solver give-up
     artifact; otherwise continue.
   - `accept`: valid only for a candidate; start fresh Pro confirmation if
     other admission limits allow.
8. Apply Pro confirmation:
   - `accept` with matching hash: enter `accepted_unpublished`.
   - `revise` or `give_up`: use its critique in the next solver round.
9. Publish the accepted artifact transactionally and complete.

No final answer is exposed before `published`.

## Budgets

Deep defaults:

- 10 solver rounds
- 8 hours of active execution time
- 4,000,000 observed cumulative tokens

Hard configuration caps:

- 20 solver rounds
- 24 hours of active execution time
- 10,000,000 observed cumulative tokens

Round admission is strict. Active-time admission is strict while the service is
running; the orchestrator also schedules best-effort deadline cancellation for
an active call. If the host is unavailable, exact remote cancellation cannot be
guaranteed. Downtime during a known active Azure call is conservatively counted
until terminal time or recovery observation.

The token threshold is explicitly soft. It sums each solver, verifier,
confirmation, and recovery call's reported `total_tokens`. A call may overshoot
the threshold because exact usage is known only after completion. The completed
call is checkpointed, then no new call begins. The UI must say "observed token
stop" and disclose possible one-call overshoot.

A Standard acceptance cannot bypass other admission limits. If Pro confirmation
cannot be admitted, the run ends as `budget_exhausted`, not solved. Users may
increase absolute limits and resume.

## Incomplete Responses and Retries

- Confirmed terminal zero-work `server_error` uses the existing bounded retry
  policy, with every attempt recorded in the call ledger.
- Ambiguous create failures are never automatically resubmitted.
- A `max_output_tokens` incomplete response receives one same-phase recovery
  attempt requesting a complete, more concise structured result.
- A second truncation pauses with `needs_attention`.
- Content filtering, refusal, or invalid structured output pauses and does not
  advance the run.
- Transient retrieval failures use bounded polling retries.
- All recovery calls count toward active time and observed tokens.

The long-run adapter must not reuse request-scoped `stream_response()`, whose
generator cancellation intentionally cancels Azure. It needs dedicated create,
retrieve/poll, detach, and cancel operations.

## Persistence

### Stable storage

Run registry state is independent of mutable transcript `save_dir`:

```text
.aoai-chat/
  long-runs.db
  long-run-control.key
  long-run-artifacts/
    sha256/
      ab/
        <full-sha256>.txt
        <full-sha256>.json
```

Each run pins:

- endpoint origin and Azure cloud
- deployment and verified capability fingerprint
- transcript path and conversation ID
- solver/verifier configuration
- original limits

Endpoint, deployment, or transcript-directory changes are rejected while a run
is active unless they do not affect the pinned run.

### SQLite schema responsibilities

The schema includes:

- `long_runs`: phase, round, limits, usage, elapsed time, revision, pinned
  configuration, accepted hash, publication state, pause reason
- `long_run_calls`: durable Azure call ledger
- `long_run_rounds`: role artifact hashes and verdict relationships
- `long_run_events`: replayable ordered events
- `long_run_idempotency`: start/mutation idempotency records
- `long_run_ownership`: global singleton and executor fencing lease
- `conversation_leases`: exclusive transcript mutation ownership for normal
  chat, Long Working Start/publication, and deletion
- `conversation_revisions`: monotonically increasing revision and projected
  transcript content hash for every conversation

A partial unique index enforces at most one globally active or resumable run.
Conversation ownership is bidirectional:

- normal chat acquires a durable conversation lease before opening SSE,
  heartbeats it during the request, and verifies its fencing token before
  transcript commit
- Long Working Start atomically acquires the global run slot and conversation
  lease
- deletion acquires the same exclusive lease before unlinking
- a conflicting operation returns `409 conversation_busy`
- expired leases may be reclaimed only with a higher fencing token

This prevents both race directions: an active run blocks stale chat/deletion,
and chat/deletion that started first block Long Working Start. Other
conversations remain usable.

Every successful transcript mutation increments its conversation revision in
the same transaction that authorizes the fenced projection. Existing
transcripts are lazily registered at revision `1` using their current content
hash. If an externally edited Markdown file has a different hash, the next
read increments the revision before returning it.

SQLite transactions provide the linearization points for:

- global run acquisition
- conversation lease acquisition and fenced transcript mutation
- call planning and acceptance
- usage and budget settlement
- Stop versus accepted-proof publication
- accepted artifact selection
- publication and completion

### Proof artifacts and transcript projection

The solver candidate is canonical UTF-8 bytes stored under its SHA-256. Standard
and Pro verifiers receive those exact bytes and hash.

The accepted artifact is authoritative. Verification summary is separate
metadata and is never concatenated to proof content.

The Markdown transcript stores an additive long-run turn reference:

```json
{
  "kind": "long_run",
  "long_run_id": "run_...",
  "long_run_status": "completed",
  "accepted_sha256": "..."
}
```

While running, the assistant side is a placeholder resolved through the run
API. On publication, the conversation API resolves assistant `content` from
the accepted artifact bytes. Markdown is updated as an idempotent projection,
but trimming or heading parsing cannot change the authoritative proof hash.

Publication phases:

```text
accepted_unpublished -> published -> completed
```

Recovery may repeat the Markdown projection safely. The conversation API must
never report `completed` with a placeholder.

Internal solver/verifier response IDs are never exposed as the conversation's
normal `response_id`.

## Recovery and Execution Ownership

The backend scheduler uses a fenced executor lease stored in SQLite. The lease
has an owner UUID and monotonically increasing fencing token. All state-changing
transactions verify the current fencing token. In-process locks reduce local
contention but are not the correctness mechanism.

Normal chat and deletion use the same fencing pattern for conversation leases.
A stale chat response whose lease expired cannot commit its transcript snapshot.

On startup:

1. Acquire or renew the scheduler lease.
2. Load nonterminal runs.
3. For an accepted call with a response ID, retrieve/poll the same response.
4. For a terminal call lacking an artifact, reconstruct and materialize it.
5. For an artifact whose run has not advanced, validate its call envelope and
   advance idempotently.
6. For `submitting` without an ID, enter `submission_unknown`.
7. Resume only fully checkpointed states.

Conversation leases are recovered separately. An expired lease may be reclaimed
only after confirming that its previous owner is not current; all later writes
from the old fencing token are rejected.

Graceful service shutdown stops admitting calls and persists any observed
response ID, but does not cancel Azure. Local polling detaches; startup recovery
continues the same response.

Explicit Stop transitions:

```text
stop_requested -> cancelling -> stopped
```

`stopped` requires observed remote cancellation/terminal state or proof that no
remote call was accepted. Missing response ID, cancellation timeout, or
uncertain remote state becomes `cancel_unknown`, not stopped.

Stop and successful publication race transactionally. Whichever durable commit
wins first determines the result: publication may complete before Stop, or Stop
prevents publication.

Browser disconnects have no execution effect. Startup recovery never resumes a
stopped run.

## API Surface

All additions are backward-compatible new routes or optional fields.
All run mutations authenticate with `X-Run-Control-Token`; the value never
appears in URLs, GET responses, SSE events, transcripts, or logs.

### Capability

`GET /api/deployments` remains side-effect-free and always returns its existing
`200` shape. Each deployment adds cached probe state:

```json
{
  "long_working": {
    "available": true,
    "probe_status": "verified",
    "checked_at": "2026-08-04T00:00:00Z",
    "capability_fingerprint": "sha256",
    "unavailable_reasons": []
  }
}
```

`probe_status` is `unknown`, `checking`, `verified`, `unsupported`, `error`, or
`stale`. The fingerprint covers endpoint origin, deployment, model version,
SDK version, verified efforts/modes, output limit, and structured-output schema
version. Verified results become stale after 24 hours; any result becomes stale
immediately when a fingerprint input changes.

`POST /api/deployments/{deployment}/long-working-probe` requires an
`Idempotency-Key`, persists a probe job, performs a bounded minimal structured
Responses call, and validates the static Pro/max contract. Unknown, stale, or
retryable-error state returns `202`:

```json
{
  "deployment": "gpt-5.6-sol",
  "probe_status": "checking",
  "probe_id": "probe_...",
  "probe_url": "/api/deployments/gpt-5.6-sol/long-working-probe"
}
```

The response includes `Location` and `Cache-Control: no-store`. Same-key replay
returns the original status, body, and headers. A current verified or
unsupported fingerprint returns `200` with the current result and does not
start another call.

`GET` on the same URL returns:

```json
{
  "deployment": "gpt-5.6-sol",
  "probe_status": "verified",
  "available": true,
  "checked_at": "2026-08-04T00:00:00Z",
  "expires_at": "2026-08-05T00:00:00Z",
  "capability_fingerprint": "sha256",
  "unavailable_reasons": [],
  "error_code": null,
  "retryable": false
}
```

Transitions are `unknown|stale|error -> checking ->
verified|unsupported|error`. Concurrent probes for the same fingerprint return
the same job. A process restart marks a stale `checking` job as retryable
`error/probe_interrupted`. A transient error preserves the prior verified
record for audit but `available` is true only when the current fingerprint is
currently `verified`. Verified results become stale after 24 hours. Unsupported
is authoritative and nonretryable until the fingerprint changes; it does not
expire by time. External synchronous lookup failure returns
`503 long_working_probe_unavailable`; verified incompatibility is represented
by `200` with `unsupported`.

Start requires a current verified fingerprint. Unknown/stale state returns
`409 long_working_probe_required`; verified unsupported state returns
`409 deployment_not_long_working_capable`.

### Start

`POST /api/long-runs`

Required headers:

- `Idempotency-Key: <uuid>`
- `X-Run-Control-Token: <client-generated-random-value>`

The browser generates and persists both values before the request. The backend
stores only a token hash, so an identical retry remains controllable without
persisting plaintext.

Request:

```json
{
  "conversation_id": null,
  "problem": "Prove ...",
  "deployment": "gpt-5.6-sol",
  "solver": {
    "reasoning_effort": "max",
    "max_output_tokens": 128000
  },
  "verifier": {
    "reasoning_effort": "max",
    "max_output_tokens": 128000
  },
  "limits": {
    "max_rounds": 10,
    "max_active_seconds": 28800,
    "max_observed_tokens": 4000000
  }
}
```

The server owns fixed modes: solver `pro`, verifier `standard`, confirmation
`pro+max`.

Semantics:

- Idempotency lookup occurs before singleton/conflict checks.
- Same key, body, and control-token hash: return the same run.
- Same key and body with a different token: `401 invalid_run_control_token`;
  the original token is never rotated or disclosed.
- Same key with different body: `409 idempotency_key_reused`.
- Existing active global run: `409 long_run_already_active`.
- Unknown non-null conversation: `404`.
- Active target conversation: `409 conversation_busy`.

Returns `202`, `Location`, `ETag`, and `Cache-Control: no-store`:

```json
{
  "schema_version": 1,
  "run_id": "run_...",
  "conversation_id": "conv_...",
  "status": "queued",
  "phase": "queued",
  "revision": 1,
  "state_url": "/api/long-runs/run_...",
  "rounds_url": "/api/long-runs/run_.../rounds",
  "events_url": "/api/long-runs/run_.../events"
}
```

### State

`GET /api/long-runs/{run_id}`

State separates:

- `schema_version`
- monotonically increasing `revision`
- `status` and `phase`
- `pause_reason`
- `resumable`
- `allowed_actions`
- budgets and observed usage
- public role configuration

`status` is one of `queued`, `running`, `paused`, or `terminal`. `phase` uses
the full run-phase enum defined above. Every committed externally observable
state, event, call transition, usage settlement, or allowed-action change
increments `revision` exactly once in the same transaction. Pure reads and
unjournaled keepalives do not increment it.

Returns `ETag: "<revision>"`.

```json
{
  "schema_version": 1,
  "run_id": "run_...",
  "conversation_id": "conv_...",
  "status": "running",
  "phase": "verifier_running",
  "revision": 17,
  "round": 3,
  "pause_reason": null,
  "resumable": false,
  "allowed_actions": ["stop"],
  "budgets": {
    "max_rounds": 10,
    "max_active_seconds": 28800,
    "max_observed_tokens": 4000000,
    "solver_max_output_tokens": 128000,
    "verifier_max_output_tokens": 128000,
    "hard_max_rounds": 20,
    "hard_max_active_seconds": 86400,
    "hard_max_observed_tokens": 10000000
  },
  "observed_usage": {
    "rounds_started": 3,
    "rounds_completed": 2,
    "active_ms": 123456,
    "total_tokens": 345678,
    "call_count": 5,
    "token_threshold_exceeded": false
  },
  "solver": {
    "deployment": "gpt-5.6-sol",
    "mode": "pro",
    "effort": "max",
    "max_output_tokens": 128000
  },
  "verifier": {
    "deployment": "gpt-5.6-sol",
    "normal_mode": "standard",
    "normal_effort": "max",
    "confirmation_mode": "pro",
    "confirmation_effort": "max",
    "max_output_tokens": 128000
  }
}
```

These nested object schemas and field names are reused unchanged by
`budget_updated`, mutation success bodies, and terminal state responses.

### Rounds

`GET /api/long-runs/{run_id}/rounds`

Returns artifact metadata and content resolved by hash. Internal Azure response
IDs are omitted.

### Events

`GET /api/long-runs/{run_id}/events`

Every journaled event uses a decimal SSE `id` and this envelope:

```json
{
  "schema_version": 1,
  "run_id": "run_...",
  "state_revision": 17,
  "occurred_at": "2026-08-04T00:00:00Z",
  "payload": {}
}
```

Journaled SSE `event` names and payloads:

| Event | Required payload fields |
|---|---|
| `run_state` | complete public state representation |
| `agent_started` | round, role, mode, effort, attempt |
| `reasoning_progress` | round, role, coalesced summary tail, character count |
| `agent_completed` | round, role, artifact hash, metrics |
| `round_completed` | round, solver hash, verifier verdict |
| `budget_updated` | budgets and observed usage |
| `terminal` | final state, allowed actions, accepted hash if any |
| `error` | RFC 9457 problem payload |

Payload field types are exact:

- `agent_started`: `round` integer, `role` enum
  `solver|verifier|confirmation`, `mode` enum `standard|pro`, `effort` string,
  `attempt` integer
- `reasoning_progress`: `round` integer, `role` enum, `summary_tail` string,
  `reasoning_chars` nonnegative integer
- `agent_completed`: `round` integer, `role` enum, `artifact_sha256` string,
  `thinking_ms` nonnegative integer, `total_tokens` integer or null,
  `reasoning_tokens` integer or null
- `round_completed`: `round` integer, `solver_artifact_sha256` string,
  `candidate_sha256` string or null, `verdict` enum
- `budget_updated`: full `budgets` and `observed_usage` objects from public state
- `terminal`: `status`, `phase`, `revision`, `allowed_actions`, and
  `accepted_sha256` string or null
- `error`: complete `application/problem+json` object

Replay is strictly `id > cursor`. No cursor emits `run_state` with SSE `id`
equal to the journal high-water mark, then tails events with larger IDs. An
empty journal has high-water mark `0` and emits snapshot `id: 0`. Browser reload
supplies `?after=<stored-id>`;
native EventSource reconnects also send `Last-Event-ID`. The header takes
precedence over the query cursor so automatic reconnect can advance beyond the
original URL. The server snapshots the replay boundary and registers the live
tail in one transaction, preventing a gap.

Malformed cursors return `400`; a cursor ahead of the high-water mark returns
`409`; an expired cursor returns `410` with `earliest_event_id`,
`latest_event_id`, and `reset_url`. Keepalives are unjournaled, id-less comment
frames. A runtime `error` event closes only if its transaction moves the run to
a terminal phase; paused/resumable errors are followed by `run_state` and the
stream remains available. `terminal` is emitted only for phases classified
terminal in the authoritative table, is journaled once, is replayable, and
closes the live stream.

The current POST-chat parser ignores SSE IDs and must not be reused. A dedicated
Long Working EventSource client persists the latest event ID per run.

### Stop

`POST /api/long-runs/{run_id}/stop`

- Requires `X-Run-Control-Token` and `Idempotency-Key`.
- Stop is unconditional and does not require `If-Match`.
- First accepted Stop returns `202`.
- Replay with the same idempotency key returns the original `202` status, body,
  ETag, and headers even if state has since advanced.
- A new idempotency key against an already terminal/stop-requested run returns
  `200` with `changed:false`.

```json
{
  "run_id": "run_...",
  "changed": true,
  "status": "running",
  "phase": "stop_requested",
  "revision": 18,
  "allowed_actions": []
}
```

The response includes the new `ETag`.

### Extend limits

`PATCH /api/long-runs/{run_id}/limits`

- Requires `X-Run-Control-Token`, `Idempotency-Key`, and `If-Match`.
- Values are absolute monotonic increases.
- Supports round, active-time, observed-token, solver-output, and
  verifier-output limits.
- Missing `If-Match`: `428`.
- Stale revision: `412` with current state.
- Decrease or hard-cap violation: `422`.

Exact request schema:

```json
{
  "max_rounds": 12,
  "max_active_seconds": 36000,
  "max_observed_tokens": 5000000,
  "solver_max_output_tokens": 128000,
  "verifier_max_output_tokens": 128000
}
```

Every property is optional, at least one is required, and additional properties
are forbidden.

Success returns `200`, the complete public state representation, and its new
`ETag`.

### Resume

`POST /api/long-runs/{run_id}/resume`

- Requires `X-Run-Control-Token`, `Idempotency-Key`, and `If-Match`.
- Allowed from stopped runs, repaired budget exhaustion, or explicitly
  resumable `needs_attention`.
- `submission_unknown` cannot resume in place. The user may wait, abandon, or
  create a new cloned run from the last safe checkpoint with explicit duplicate
  cost acknowledgement.
- Invalid transition or unmet prerequisite: `409`.

Success returns `202`, the complete public state representation, and its new
`ETag`. Idempotent replay returns the original success representation.

### Resolve ambiguous recovery

`POST /api/long-runs/{run_id}/recheck`

- Requires control token, `Idempotency-Key`, and `If-Match`.
- Allowed from `submission_unknown` and `cancel_unknown`.
- Re-runs only safe retrieve/status checks; it never creates a response.
- Returns `202` if checking continues or `200` if state resolves.
- Body is the complete public state plus `changed`; response includes the
  resulting ETag. Idempotent replay returns the original response before
  evaluating current `If-Match`.

`POST /api/long-runs/{run_id}/abandon`

- Requires control token, `Idempotency-Key`, and `If-Match`.
- Marks the run `abandoned`, releases the global slot and conversation lease,
  and records that an unknown Azure job may still incur cost.
- It never claims remote cancellation.
- Legal source phases are `stopped`, `budget_exhausted`, `needs_attention`,
  `submission_unknown`, and `cancel_unknown`. Active call phases must Stop
  first.
- Returns `200` with complete terminal public state, `changed`, and ETag.

`POST /api/long-runs/{run_id}/clone`

- Requires source control token, `Idempotency-Key`, `If-Match`, and
  `X-New-Run-Control-Token`.
- Body must contain:

```json
{
  "acknowledgement": "resubmit_despite_possible_duplicate"
}
```

- Allowed only from `submission_unknown` after the latest recheck remains
  unresolved.
- In one transaction, abandons the source, releases its singleton ownership,
  creates a new run from the last safe artifact, and acquires ownership for the
  clone.
- The idempotency record binds both source and new control-token hashes; replay
  with a different new token returns `401`.
- Returns the normal `202` Start representation, `Location`, and clone `ETag`.

After authenticating the source control token, mutation idempotency lookup
precedes `If-Match` and transition checks. All records bind method, run, request
hash, original control-token hash, success body, status, headers, and ETag. Same
key with a different request returns `409`; same key with a different token
returns `401`.

Stop, Resume, Recheck, and Abandon accept either no body or exactly `{}`; any
property returns `422 validation_error`. Clone and limits require their defined
bodies. Validation precedence is:

1. route/run existence
2. control-token validation
3. idempotency replay/conflict
4. body/schema validation
5. `If-Match`
6. transition and domain validation

Thus simultaneous token and body mismatch returns `401`; a valid-token replay
returns its original response before stale revision evaluation. Every `412`
uses the normal problem shape with numeric top-level `status`, includes the
complete run representation under `current_state`, and returns the current
`ETag` header:

```json
{
  "type": "https://aoai-chat.local/problems/revision-stale",
  "title": "Run revision is stale",
  "status": 412,
  "code": "revision_stale",
  "detail": "Refresh state and retry with the current ETag.",
  "run_id": "run_...",
  "revision": 18,
  "allowed_actions": ["refresh"],
  "current_state": {
    "schema_version": 1,
    "run_id": "run_...",
    "status": "paused",
    "phase": "budget_exhausted",
    "revision": 18
  }
}
```

The abbreviated `current_state` above represents the complete public state
schema defined by the State endpoint.

### Errors

Errors use RFC 9457-style JSON:

```json
{
  "type": "https://aoai-chat.local/problems/run-not-resumable",
  "title": "Run cannot be resumed",
  "status": 409,
  "code": "run_not_resumable",
  "detail": "The current Azure submission is ambiguous.",
  "run_id": "run_...",
  "revision": 18,
  "allowed_actions": ["recheck", "abandon", "clone"]
}
```

All problem responses use `application/problem+json`. The stable route/condition
contract is:

| Condition | HTTP | Code |
|---|---:|---|
| Missing control token | 401 | `run_control_token_required` |
| Invalid/different control token | 401 | `invalid_run_control_token` |
| Missing idempotency key | 400 | `idempotency_key_required` |
| Reused key with different request | 409 | `idempotency_key_reused` |
| Long-run route has unknown run | 404 | `long_run_not_found` |
| Start has unknown non-null conversation | 404 | `conversation_not_found` |
| Chat has unknown conversation with integer revision | 404 | `conversation_not_found` |
| Another global run owns the slot | 409 | `long_run_already_active` |
| Conversation mutation lease conflict | 409 | `conversation_busy` |
| Delete while run retains lease | 409 | `conversation_has_active_long_run` |
| Long-run probe required | 409 | `long_working_probe_required` |
| Deployment verified unsupported | 409 | `deployment_not_long_working_capable` |
| Probe service unavailable | 503 | `long_working_probe_unavailable` |
| Missing `If-Match` | 428 | `revision_required` |
| Stale `If-Match` | 412 | `revision_stale` |
| Stale optional chat revision | 409 | `conversation_revision_stale` |
| Internal chain supplied for rehydrate | 409 | `conversation_requires_rehydrate` |
| Resume from active or terminal phase | 409 | `run_not_resumable` |
| Resume from `submission_unknown` | 409 | `submission_unknown` |
| Resume from `cancel_unknown` | 409 | `cancel_unknown` |
| Resume budget exhaustion before extension | 409 | `budget_extension_required` |
| Recheck outside unknown phases | 409 | `recheck_not_allowed` |
| Abandon during an active call phase | 409 | `abandon_requires_stop` |
| Clone outside unresolved submission | 409 | `clone_not_allowed` |
| Any other illegal phase transition | 409 | `invalid_run_transition` |
| Limit decrease | 422 | `limit_decrease_not_allowed` |
| Limit exceeds hard cap | 422 | `limit_hard_cap_exceeded` |
| Invalid request/schema | 422 | `validation_error` |
| Malformed cursor | 400 | `event_cursor_invalid` |
| Cursor ahead of journal | 409 | `event_cursor_ahead` |
| Cursor older than retention | 410 | `event_cursor_expired` |
| Ambiguous accepted submission | 409 | `submission_unknown` |
| Ambiguous cancellation | 409 | `cancel_unknown` |

## Conversation Integration

Optional additive fields:

```json
{
  "revision": 7,
  "kind": "long_run",
  "long_run_id": "run_...",
  "long_run_status": "running"
}
```

Absent `kind` means a normal turn. Existing roles, content, turn count, and
response status retain their current meanings. Conversation summary and detail
responses always return a non-null integer `revision >= 1`. Each successful
normal turn commit, long-run placeholder/publication update, external-content
hash reconciliation, or other transcript mutation increments it once.

While a run is active:

- normal chat against that conversation returns `409 conversation_busy` before
  opening SSE
- deletion returns `409 conversation_has_active_long_run`
- other conversations remain usable

The same rules apply while a run is safely resumable or unresolved:
`budget_exhausted`, `stopped`, `needs_attention`, `submission_unknown`, and
`cancel_unknown` retain the global slot and conversation lease. Completion,
give-up, nonresumable failure, or explicit abandon releases them.

The reverse race is also blocked. Normal chat or deletion that already holds
the conversation lease causes Long Working Start to return
`409 conversation_busy`. Normal chat verifies its fencing token again before
transcript commit; stale completion cannot overwrite a later long-run
projection.

After any terminal state that releases the lease, normal chat starts a clean
Responses chain from server-owned visible transcript content:

```json
{
  "continuation": {
    "mode": "transcript_rehydrate",
    "previous_response_id": null
  }
}
```

`/api/chat` loads the authoritative transcript under the conversation lease,
constructs Responses input from visible user/assistant content, appends the new
user message exactly once, and starts without `previous_response_id`. The
additive `ChatRequest.conversation_revision: integer|null` field carries the
last revision returned by `GET /api/conversations/{id}`. It remains optional for
backward compatibility. Omitted or explicit `null` means no revision
precondition and uses the latest server revision. An integer mismatch returns
`409 conversation_revision_stale`. The frontend sends an integer after loading
a long-run conversation but does not supply authoritative history.

Legacy create-on-missing behavior remains for `/api/chat` when
`conversation_revision` is omitted or null: an unknown supplied conversation ID
creates that conversation. If an integer revision is supplied for an unknown
conversation, `/api/chat` returns `404 conversation_not_found`. Long Working
Start always returns `404` for an unknown non-null conversation because Start
must acquire an existing conversation lease transactionally.

Existing behavior is preserved when `previous_response_id` is present: the
Responses path continues that chain and ignores client `history`. When a
conversation is marked `transcript_rehydrate`, `previous_response_id` must be
null or the server returns `409 conversation_requires_rehydrate`; client
`history` is ignored and server transcript content is authoritative. Chat
Completions fallback retains its existing client-history behavior. In every
path, the new user content is appended exactly once.

Internal solver/verifier chains are never used for ordinary follow-up chat.

Deletion behavior is consistent: Stop pauses safely but retains resumability and
therefore still blocks deletion. The user must complete, give up, reach a
nonresumable failure, or explicitly abandon the run. Deletion then removes both
transcript projection and run artifacts before returning the existing
`200 {"deleted","filename"}` shape with optional cleanup fields.

## User Experience

Long Working Mode is a toggle next to Send. Enabling it opens a configuration
sheet with:

- deployment capability/probe status
- solver effort and output budget; Pro mode shown as locked
- verifier effort and output budget; Standard normal mode shown as locked
- fresh Pro final-confirmation policy
- round, active-time, and soft observed-token thresholds
- explicit high-cost and one-call-overshoot warnings

The conversation renders:

- overall run card with phase, round, active time, observed tokens, last update,
  and Stop
- collapsed solver draft and verifier critique cards per round
- metrics, verdict, confidence, and candidate hash identity
- a distinct fresh Pro confirmation state
- precise terminal cards for success, give-up, budget exhaustion, stop,
  cancellation ambiguity, failure, and attention-required recovery

On success, the assistant content is exactly the accepted artifact. A separate
metadata summary says `Model-verified: Standard + independent Pro`. Expanded
artifacts remain available under the run card.

Navigation is allowed while the run continues. Sidebar badges expose running,
paused, and completed states. Version one permits one global Long Working run;
ordinary chat in other conversations remains available.

## Testing Strategy

### State-machine tests

- every solver/verifier verdict combination
- Standard acceptance followed by fresh Pro acceptance or rejection
- candidate-hash mismatch at either verifier
- candidate and give-up artifact transition matrix, including invalid
  `give_up + accept`
- strict round and active-time admission
- soft token threshold and one-call overshoot
- output truncation and one recovery attempt
- refusal, filtering, malformed output, and retrieval failures
- Stop before ID, during execution, and between phases

### Transaction and concurrency tests

- two simultaneous Start requests: one active run
- idempotent Start retry with same/different body
- same-conversation normal chat and deletion return `409`
- chat-first/Start-second and deletion-first/Start-second return `409`
- expired conversation lease reclamation and stale chat commit rejection
- other-conversation foreground chat remains available
- stale executor fencing token cannot mutate state
- stale `If-Match` returns `412`
- Stop/publication race has one durable winner

### Crash tests

- crash after `submitting` before response ID -> `submission_unknown`
- crash after response ID -> retrieve same response
- crash after terminal before artifact -> materialize from Azure
- crash after artifact before state advance -> idempotent advance
- crash at each publication phase -> no completed placeholder
- graceful shutdown detaches without remote cancellation

### API and SSE tests

- control-token hashing and restart persistence
- idempotency response semantics
- recheck, abandon, and clone transitions release/acquire singleton ownership
- event high-water snapshot and gap-free replay
- malformed/ahead/expired cursor errors
- terminal emitted once
- RFC 9457 error fields and allowed actions
- internal response IDs never exposed

### Browser tests

- configure and start a run
- collapse/expand round cards
- reconnect using SSE event IDs
- navigate away while execution continues
- Stop and cancellation ambiguity
- extend budget and resume
- deletion blocked through stopped/resumable states until abandon or terminal
- exact proof plus separate verification summary

### Live Azure and quality evaluation

A bounded live smoke test uses a small problem and at most two rounds to verify
structured-output capability, solver/verifier chaining, fresh Pro confirmation,
persistence, and reconnect.

A curated evaluation set includes straightforward proofs, subtle false lemmas,
false statements requiring counterexamples, underspecified problems, and known
edge cases.

Track solve rate, false acceptance rate, rounds, active time, observed tokens,
Standard/Pro disagreement, and give-up accuracy. False acceptance is the
primary quality metric.

## Acceptance Criteria

- A partial SQLite uniqueness constraint enforces one active run globally.
- Solver calls always use Pro mode.
- Normal verifier calls use Standard mode.
- Final confirmation is fresh, unchained, Pro+max, and hash-bound.
- Standard acceptance alone can never complete a run.
- Both verifiers accept the same exact candidate hash.
- Assistant content resolves to the accepted content-addressed proof bytes.
- Verification summary is separate metadata.
- Both-role give-up agreement references the same hashed solver artifact.
- Browser disconnect does not stop execution.
- Known Azure calls recover without duplicate submission.
- Unknown submissions never auto-resubmit.
- Round admission is strict; token threshold is explicitly soft.
- Stop does not report stopped while remote state is uncertain.
- Recheck, abandon, and explicit clone resolve ambiguous runs without silent
  resubmission.
- Bidirectional fenced conversation leases block both chat-first and run-first
  mutation races.
- Same-conversation chat and deletion stay locked through resumable states.
- Other conversations remain usable.
- Server-owned transcript rehydration starts ordinary follow-up without
  internal agent response IDs.
- SSE replay is gap-free and cursor recovery is typed.
- Existing normal chat and transcript responses stay backward-compatible.
- Outcomes are labeled model-verified, not mathematically certified.

## References

- Microsoft, "Use the Azure OpenAI Responses API":
  https://learn.microsoft.com/azure/foundry/openai/how-to/responses
- Microsoft, "How to use structured outputs with Azure OpenAI":
  https://learn.microsoft.com/azure/foundry/openai/how-to/structured-outputs
- OpenAI, "Structured model outputs":
  https://developers.openai.com/api/docs/guides/structured-outputs
