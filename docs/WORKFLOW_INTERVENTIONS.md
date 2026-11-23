# Human intervention and durable research execution

FOREST records the accepted intent, the affected runs and the observed execution
effects separately. The Overview page shows **Human instructions and effects**,
including individual target failures and pending human decisions. Saving an edit
or accepting a control request does not establish that its external effects have
finished.

## Edit, preview and stop

Node edits retain the graph revision observed by the editor. A dirty draft stays
local when another editor changes the node; saving it opens a conflict review.
Load the current content and review the change before resubmitting. Advanced
graph previews also retain their original revision. Applying an old preview
returns a conflict instead of silently extending its scope to newly added work.

**Stop current run when saving** includes queued, paused and running targets.
An undispatched run is cancelled without starting an executor. Active targets
remain fenced while FOREST reconciles their local process, container or SSH job.
Inspect each target's receipt; an uncertain or partial effect requires attention.
Process pause waits for FOREST database transactions to finish before suspending
the executor, so a paused task cannot retain a transaction lock needed to record
its control receipt. A busy transaction can leave the request pending or uncertain
until a safe pause boundary is reached.
The durable outbox retries reconciliation after restart. It does not promise
exactly-once behavior for arbitrary external services.

Pruning stops future branch dispatch and durably holds queued runs for an owner
choice. Existing running work continues under that scheduling-only operation.
Restoring the branch does not launch held runs: restore it, then explicitly resume
the selected runs. Use run cancellation to stop existing execution.

Single commands, batches, selected proposals, agent graph actions and planner
adoption use the same application service. A batch accepts at most 200 commands
and retains one undo snapshot. Undo restores editable graph structure; it does
not reverse completed external actions or erase run history.

Workspace forks and merges use source-bound file plans. New branches stay
materializing and cannot execute or accept owner file edits until their copy is
confirmed. Changed source bytes invalidate an uncompleted copy. After a crash,
FOREST checks published files and its durable receipt before adopting a copy.

## Instructions and evidence use

Ordinary comments are annotations. Use **Send as instruction** for an explicit
project, branch, node or active run instruction. Select the next model request
or future new runs as its boundary. A request already in flight retains its
original context. The receipt becomes applied only after the current instruction
was included in a successful physical model request, either whole or through
complete recorded context pages. This proves delivery, not semantic obedience.

Ordinary node method and input edits apply to new runs. Historical retry retains
its saved configuration and provider snapshot; **Run node** uses the current
node. Both retain their execution context and node revision.

Changing a Goal preserves completed measurements and their computational
verification. Their use against the current Goal may require an explicit reuse
or exclusion decision with a reason. A scoped Goal affects only its declared
scope. Planner comparisons, figures and manuscripts check current applicability.
Structured dependencies identify affected metrics, claims, figure blocks and
manuscript locations; a prose mention of an ID does not create a dependency.

## Tools, approvals and budgets

Tool permissions intersect the role, project, node and run settings. An omitted
list inherits; `[]` permits no tools. Revocation is checked again before pending
and resumed actions execute. Disabling a role also prevents its saved actions.

Set `human_review_tools` to an explicit list or `["*"]`. Before a selected action,
FOREST saves its identity and parameters and yields the executor. The decision
survives API and worker restarts and releases the scientific worker slot. Review
the exact action, then accept, edit or reject it. Changes to its reviewed state
invalidate an old approval. **Continue saved decision** resumes an already
answered decision without answering it again. Remaining budget is checked on
resume; a saved approval does not authorize additional budget.
Cancelling a task closes its outstanding decisions. Pending or unused accepted
actions become stale; a saved rejection remains rejected and loses its continue
control. The original answer stays available in history.

Project budgets accept nonnegative finite `seconds` and `cost_usd`, and boolean
`allow_paid`. Legacy nonnegative integer `max_runs` is retained as metadata;
counting prior runs does not limit research lifetime. Paused live work continues to count
wall time. Resume and new dispatch preserve prior consumption and other runs'
reservations. A detached container stays paused until a replacement executor is
admitted. An unknown model charge remains unknown rather than being reported as
zero.

## Scheduling and model selection

Run mode and autonomous planning are separate saved controls. Manual mode defaults
autonomy off when no explicit choice exists. Starting or continuing passes the
saved choice. Set **Independent task slots** from 1 to 32 to bound independent
ready work; the default is one. Dependency barriers and resource admission still
apply. A writer's verifier cannot consume its output before the writer completes.

For new runs, provider selection follows explicit run override, node, role,
project, global setting, then an available provider. The run records the selected
source. An explicit missing provider is an error. Change a started run's provider
by creating a new run rather than rewriting its historical transport identity.

The `codex_cli` provider uses the host's signed-in Codex CLI. Its inference child
has no native shell, MCP, app or browser tools; it returns FOREST action JSON for
FOREST to validate and execute. Request digests identify the actual CLI prompt.
Subscription token usage is recorded, while USD cost and a hard output-token
ceiling are not supplied by this transport. Unpriced requests make the total
USD estimate and remaining USD unknown. `known_cost_usd` reports only the priced
subtotal; `unknown_cost_requests` identifies incomplete accounting.

## Purpose-specific scientific handoffs

Select an acceptance purpose in execution settings. Source evidence must belong
to this project, be completed, independently checked and applicable to the current
Goal. The purposes impose additional conditions:

| Purpose | Required evidence |
| --- | --- |
| `exploratory` | No scientific acceptance claim. |
| `raw_data` | Explicit `artifact_paths` with source-bound `data_contract` checks for schema, units, sample identity and declared split. |
| `comparison` | Complete independently recomputed numerical coverage and matching declared comparison conditions. |
| `major_claim` | Comparison conditions plus distinct completed `research_phase: "confirmation"` runs with independent held-out evaluation, independent checks and current Goal applicability. |

Feedback-linked and unclassified older evaluations can still serve in comparisons.
For a major-claim handoff, run and independently verify a distinct confirmation
with an evaluation classified as `independent_confirmation` and free of feedback.

For example, configure a downstream comparison with actual source run IDs:

```json
{
  "run_ids": ["actual-baseline-run-id", "actual-candidate-run-id"],
  "acceptance_contract": {"purpose": "comparison"}
}
```

A missing handoff makes the consumer wait before execution. Changed verified
files, comparison conditions or confirmation provenance invalidate admission.
These gates check declared data and execution properties. Scientific importance,
protocol quality and publication readiness still require review. A
`full_submission` project also requires its complete measured experiment matrix,
actual raw-data handoffs, comparisons and confirmation scope; an operational
replication does not certify a complete scientific submission.

## Restart, import and restore

Use the supported `forest serve` deployment and the backup/restore commands in
[deployment guidance](DEPLOYMENT.md). Normal service restarts reconcile active
execution by its recorded identity. Lost identity or an unconfirmed control is
reported explicitly instead of starting replacement work blindly.

Portable imports and duplicates retain inspectable control history without
transferring execution authority. Imported effects are superseded, decisions
stale and active runs interrupted; pending workspace branches are disabled.
Backup restoration likewise quarantines pending controls and pauses controllers.
Review the recovered project and explicitly authorize fresh execution.

For CLI node commands with `--request-id`, the client persists the original
request before sending it. Repeating that ID with the same input replays the
original observed revision and defaults, including after a lost response. A
different input requires a new ID. Request records are stored alongside the CLI
configuration, contain no connection token and are not a cache of server state.
