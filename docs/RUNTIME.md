# Research runtime

FOREST stores research state in a database and editable project files. The API accepts graph and project edits, the scheduler resolves execution dependencies, and workers run the selected agent or experiment. A model provider supplies reasoning and tool requests; process supervisors execute commands and record observed outcomes.

## Context and continuity

The retained project state includes current goals, controls, graph revisions, run records, artifacts and public action history. `ContextBuilder` selects the current node's relevant branch and input material. The agent's context store retains original controls and recorded public history for paginated retrieval. Source excerpts point to files that remain separately readable through workspace tools. Required original control pages must be read before task actions become available.

Each model request has a finite working context. Packing keeps complete native tool exchanges or replaces an exchange with a reference to its saved public record. Provider window errors trigger smaller requests and retrieval under the same spending controls. Long project histories can remain accessible across many requests; retaining a record and understanding its contents are separate operations.

When a complete JSON response is explicitly truncated, output-split recovery
accepts one independently valid object per chunk and requires a total from 2 to
64 chunks for that one assembly. Recovery records at most the first 16 missing
indexes, plus the exact missing count, a truncation flag, and the next required
index. This is an output assembly bound only. It is not a global cap on the
task, retained history, or context read steps.

Agent turns and planning cycles do not bound research lifetime. Each executor works for a bounded round, writes an atomic session checkpoint, then yields as `waiting` with `wait_for.type=work_round`. The worker automatically continues the same run and output directory from that checkpoint. Cumulative turns are audit data. Project/provider token, spending and active-time limits still apply to every round and branch; reaching an actual resource hard limit is recorded separately from successful completion.

Set `agent_execution.execution_round.steps` in project configuration, or `execution_round.steps` in a run, to choose the technical work-round size. The default is 30 turns per round. This value controls checkpoint/dispatch cadence; there is no maximum round count or cumulative-step limit. A round that stalls enters diagnosis and graph-aware replanning rather than buying more turns or starting research again.

## Execution and recovery

An agent can start a managed process, inspect it, read incremental output, cancel it, or yield while it runs. Yielding releases the agent executor slot; the worker resumes the same session after the wait condition changes. Real process identities and completion receipts determine execution status. Missing exit information produces a lost or interrupted state.

Action identities protect supported tool submissions from duplicate execution after a retry. Supervisors can survive an agent interpreter exit. A restarted worker can reconnect to an existing process or container after checking its identity. If the computation itself has stopped, continuation requires the task's checkpoint and recovery configuration. The task owns the completeness of its saved scientific state.

Work-round continuation preserves the run identity, action journal, managed-process receipts and accumulated resources. A legacy step-only `budget_exhausted` checkpoint is migrated to round continuation when loaded; saved token, cost and time exhaustion remains a resource block. Legacy `max_steps`, `agent_budget.steps`, `step_budget_policy` and controller `max_cycles` values remain inspectable as historical configuration, but cannot end a research lifecycle. No new project or run is needed for ordinary round continuation. Lost paused Agent executors can be resumed from a matching valid session checkpoint; a missing or mismatched checkpoint remains a recorded recovery error. Unreadable or invalid supervisory ledgers require reconciliation; session summaries return unknown usage with a diagnostic instead of zero consumption.

Scientific checkpoints contain goals, decisions, graph references, evidence, job identities and next actions. Cumulative token, cost and active-time accounting is stored separately in the run's `agent_usage.json`, outside its scientific workspace, and the shared project/provider ledger. Checkpoint reconstruction cannot reset that ledger. The full session read API exposes scientific state; its compact summary has an explicit `usage` field read from the separate matching resource ledger, with read compatibility for older workspace-local ledgers. The evidence panel identifies automatic round continuation separately from a managed-process wait. Research Graph revisions and original evidence remain available when the next round rebuilds a focused model context.

Completion checks reject active managed processes and missing required output files. Numeric file observations require a successful managed process receipt. Inspect the commands, inputs and outputs when evaluating a result: existence and successful execution establish a narrower property than a sound experimental method.

## Editable dependencies

Graph commands require the revision that the editor read. They check project boundaries, node and edge references, and acyclic execution dependencies before publishing the revised graph. The scheduler checks current input bindings and producer runs before dependent work starts.

The impact analyzer follows declared dependencies and explicit input references. A method or data change can require recomputation; a statistical change can require renewed analysis; a wording or layout change can require rendering. This classification keeps repairs local when the declared dependencies are complete. External files and services must be represented in the task's inputs if their changes should participate in this reasoning.

## Research decisions and manuscript production

Research analysis records a best estimate, uncertainty, contrary evidence and the experiment that would change the decision. Experiment protocols identify the comparison, statistical unit, selection procedure and argumentative duty. Saved predictions support independent numerical checks. Reviewer contexts can omit earlier judgment summaries while retaining primary evidence.

Manuscript generation reads selected completed-run artifacts. Bound values resolve to saved measurements; cited records and declared critical contrary references are checked before rendering. Unbound numeric prose is flagged for review. Paper layout jobs preserve editable source and publish compiled output against its source revision. See [the evidence workflow](EVIDENCE_WORKFLOW.md) and [paper authoring](PAPER_AUTHORING.md) for the full contracts.

## Source map

| Responsibility | Implementation |
| --- | --- |
| Context selection and retained records | [`research/kernel/context.py`](../research/kernel/context.py), [`research/agents/context_store.py`](../research/agents/context_store.py) |
| Agent decisions and tool lifecycle | [`research/agents/runtime.py`](../research/agents/runtime.py), [`research/agents/policy.py`](../research/agents/policy.py) |
| Graph edits and affected work | [`research/kernel/graph.py`](../research/kernel/graph.py) |
| Scheduling and recovery | [`services/worker/scheduler.py`](../services/worker/scheduler.py), [`services/worker/main.py`](../services/worker/main.py) |
| Evidence and manuscript checks | [`research/paper/evidence.py`](../research/paper/evidence.py), [`research/validation/protocol.py`](../research/validation/protocol.py) |

See [deployment](DEPLOYMENT.md) for service management, isolation and backups, and [the design note](DESIGN.md) for the assumptions behind the reliability argument.

## Versioned research checkpoints and reconciliation

`research/agents/lifecycle.py` controls bounded work rounds. A boundary creates a durable `work_round` wait and releases the worker; the scheduler resumes the same TaskRun automatically. `round_state` records technical execution independently of `research_state`. An Agent finish records completed execution awaiting scientific verification; execution errors cannot falsify hypotheses or complete the project.

The authoritative `agent_session.json` contains `execution_snapshot` with Goal Contract, complete Research Graph, current and historical evidence, negative observations, current stage, dependency/task queue, pending action or model request, experiment parameters/seeds/data/source version, job bindings, and decision history. Each round also creates an immutable checkpoint manifest outside the editable workspace. Repeated Graph snapshots are stored once by hash; manifests reference the versioned Graph and original evidence artifacts. Context segments retain immutable content hashes and union page coverage. Unchanged authorized file pages retain their validated content and file identity, so recovery reuses observations instead of rereading protocols.

Scientific checkpoints retain `external_ledger_id`, original project/run identity, and supervisor locator; they do not duplicate cumulative GPU, token, cost or active-time state. The external usage ledger and DB model/admission records stay associated with the same run. Mismatched or missing historical accounting requires reconciliation and cannot create fresh accounting.

Before recovering an experimental job, the executor checks its committed launch identity, full local source/configuration hash, process state, completion receipt, and saved original artifact hashes. A durable launch intent precedes side effects. An ambiguous launch or model request cannot be repeated automatically. `execution_reconciliation` preserves the original job and requests reconciliation. Declared `code_inputs` bind non-argv configuration/data inputs; argv inputs and imported workspace code are also hashed. Mutable working copies may change while immutable original receipts/artifacts remain available.

Graph recovery records the triggering failure, relevant ancestors, accepted versus stale evidence, excluded/refuted/unchanged failed paths and selected existing work. Eligible alternatives are admitted through the normal scheduler with stable request identities and the original project ledger. Unverified consumers wait for actual verification. With no feasible alternative, diagnosis is bounded per unchanged scientific state across rounds; summaries, repeated reads, equivalent failed jobs, cloned nodes and irrelevant files do not renew the fuse. Materially repaired code or legitimately changed authorization allows the corresponding action to be reconsidered.
