# FOREST HTTP API reference

The dashboard and CLI use the same `/api` endpoints and saved project state.
Use this reference to replace frontend screens without duplicating research
logic, execution state or manuscript data in the UI. The live contract is
available at `/openapi.json`, with interactive documentation at `/docs` and
`/redoc`. Checked-in contracts and the TypeScript client are described in
[API contract maintenance](API_CONTRACTS.md) and the
[frontend integration guide](FRONTEND_API_GUIDE.md).

## Transport and authentication

- The default local API origin is `http://127.0.0.1:8000`. A development frontend
  proxies `/api` and `/ws` to the API; production serves the built frontend from
  the same origin. There is no version prefix or universal `{data: ...}` wrapper.
- JSON operations use `Content-Type: application/json`. Upload and project import
  use `multipart/form-data` with a `file` field; let the browser set its boundary.
- Every existing successful HTTP operation returns `200`, including creation and
  task submission. A returned run is a queued/persisted execution record, not its
  eventual result. Lists are bare arrays unless the individual endpoint says
  otherwise. Binary exports and SSE must not be read with `response.json()`.
- Protected `/api` requests accept `Authorization: Bearer <owner-token>` or the
  `forest_owner` cookie. The owner token is a FOREST credential, not a model API
  key. `/api/auth/login` sets an HttpOnly, SameSite=Strict cookie for 14 days;
  HTTPS adds Secure. API requests from a real loopback peer to a trusted hostname
  can establish the owner cookie without an existing token. Both the peer and
  hostname conditions must match; the OpenAPI empty security alternative is
  restricted by `x-forest-local-owner-access`.
- `/api/health`, `/api/auth/login`, `/api/auth/status` and GET share snapshots are
  public. DELETE share revocation still requires an existing valid owner cookie
  or bearer token. Public share snapshots expose only the selected display/status
  fields. Public-prefix middleware behavior does not authorize writes.
- On protected endpoints a supplied Origin must have a trusted hostname or match
  the request hostname; otherwise the API returns `ORIGIN_DENIED`. This is the
  existing same-origin policy, not a configurable permissive CORS layer.
- Provider/host/Agent configuration and scientific `data` fields are extensible
  objects. A component's documented properties are the known fields, not a list
  of every possible experimental payload. Persisted status/type strings remain
  open unless the existing validator explicitly defines an enum.

## Revision domains and editable paths

| Operation | `expected_revision` compares | When omitted |
| --- | --- | --- |
| Graph preview/commands | Project graph revision | Required by GraphCommand |
| Graph batch | Project graph revision | Rejected by revision comparison |
| Project PATCH, node PATCH, idea adoption, proposal application | Project graph revision | Use the current revision |
| Generic resource PATCH, figure revision | That resource's revision | Use the current revision |
| Manuscript PATCH, compile, revise, export | PaperDocument revision | Use the current revision |
| Manuscript figure insertion, layout, review application | PaperDocument revision | Rejected by revision comparison |
| File PUT | That path's FileRevision | Accept current file revision |

Node revision identifies the version used by a run; it is not the graph edit
guard. Keep project, node, resource, paper and file revision values separate in
frontend state. On `409`, refresh the applicable record, preserve unsaved edits,
and let the user merge/reapply them. Do not silently overwrite the refreshed
state or replay a rejected semantic edit against an unrelated revision.

Graph commands support `add_node`, `edit_node`, `delete_node`, `add_dependency`,
`remove_dependency`, `fork_branch`, `clone_subtree`, `insert_before`,
`insert_after`, `reparent_subtree`, `merge_branches`, `split_node`, `group_nodes`,
`prune_branch`, `restore_branch`, `set_main_branch`, `apply_instruction_patch`,
`undo` and `redo`. `targets` contains existing node IDs; branch operations can
select `params.branch_id`. `params` is operation-specific. Preview returns impact
only; command application returns the updated graph, impact and scheduling IDs.
Inspect `conflicts`, `input_bindings_required`, `actions`, `rerun_nodes` and
`refresh_nodes` before presenting an edit as complete. `run:true` requests
scheduling, not a synchronous experiment.

| Graph operation | Target / main `params` fields | Resulting editable operation |
| --- | --- | --- |
| `add_node` | Node fields directly or `node:{...}`, optional branch_id/parent_id | Add a node and optional execution parent |
| `edit_node` | targets: node IDs; fields directly or `patch:{...}`; optional stop_current_run; optional `positions:{node_id:{x,y}}` | Deep-merge editable fields, apply per-node layout positions atomically, analyze impact, optionally cancel the current run |
| `apply_instruction_patch` | targets: node IDs; instructions or old_text/new_text | Replace instructions or one exact matching passage |
| `delete_node` | targets; strategy subtree/reconnect/visual_only when descendants exist | Remove a subtree, reconnect execution reachability, or remove visual grouping |
| `add_dependency`, `remove_dependency` | source/target or two targets; relation; add supports input_mapping | Add/remove the specified typed edge |
| `fork_branch`, `clone_subtree` | origin target; optional name/node_ids/include_descendants/destination_branch_id/copy_policy | Clone selected nodes into a new or existing branch; create workspace only for a new branch |
| `insert_before`, `insert_after` | pivot target; node fields in node or selected direct fields | Insert a step and reconnect execution edges |
| `reparent_subtree` | pivot target; parent_id or new_parent_id; move_to_branch/input_map | Move execution parent and explicitly repair/review affected input bindings |
| `merge_branches` | left/right or left_branch_id/right_branch_id; name/resolution/dependencies | Compare and explicitly merge source workspaces/configuration into a new branch |
| `split_node` | original target; parts array of at least two node objects | Create a sequence, archive the original and flag dependent output rebinding |
| `group_nodes` | selected targets; optional group_id/title/collapsed | Create an editable display group without an experiment |
| `prune_branch`, `restore_branch`, `set_main_branch` | params.branch_id or a node target's branch | Change branch scheduling/active/main state |
| `undo`, `redo` | Current expected_revision; no target needed | Restore an editable graph history snapshot |

Fork copy_policy supports code/data/results strategies copy/reference/exclude
(booleans map to copy/exclude) and max_copy_bytes, default 100 MiB. Default file
policy copies code, references data and excludes results. Saved node result
handling also depends on the requested result copy policy; copied/reference
results do not establish fresh execution. Merge resolution.files maps paths to
left/right/base/delete or `{content:"..."}`; resolution.config is left/right or
an explicit configuration object. Missing merge choices return conflicts. A
missing editable fork base uses two-way comparison instead of inventing a base.

`depends_on` and `consumes` are execution relations. Typed evidence/citation/history
relations have different propagation duties. Kernel input bindings can create
execution dependencies even when no explicit canvas edge is drawn; the frontend
must preserve `inputs`, verification configuration and edge `input_mapping`.
Graph/branch/history edits remain editable. Undo changes graph content; it does
not undo completed processes, filesystem writes or external effects.

API file/artifact views use paths relative to their PROJECT root. Explicit node
`InputReference.path` instead uses its source BRANCH workspace unless
`project_scope:true`; `branch_id`/`node_id` select that actual source. Its
`destination` is relative to the receiving run workspace. Preserve these path
coordinates when moving a node or designing a file picker. `Branch.workspace` is
a project-relative directory; task `output_path` points to the run folder. Download
paths must be URL-encoded, not joined to a frontend filesystem directory.
`/projects/{id}/file` edits UTF-8 text; `/download` handles images/PDFs/binary files.
Working `paper/paper.tex` and `paper/references.bib` edits synchronize the saved
manuscript and invalidate its previous layout/compilation state.

## Submission IDs and state refresh

Graph command receipts are deduplicated by `(project_id, request_id)` before a
new command is applied. Batch commands use the same receipt namespace. Generic
scheduler submissions also use project request IDs. Keep an ID for transport
retries of the same submission and use a fresh ID for changed intent. Some
handlers compare payloads on replay (repository, verifier and layout submission);
do not assume every generic task checks payload equality. Idea adoption uses an
adoption receipt prefix. Node PATCH creates its own request ID and is not a
client-controlled idempotent graph-command replay.

Subscribe to project events after loading authoritative state. A notification is
a refresh/invalidation signal, not a complete replacement for the project/run/
resource. Re-fetch the affected records. Poll or reconnect for runs even if a
frontend component is remounted; never infer completion from a closed terminal.
Null timestamps, PID, exit code, error, session or spending limit mean no current
value. An unmeasured/null trial value is not a zero score.

## Errors

Application errors normally have:

```json
{"detail":{"code":"REVISION_CONFLICT","message":"Project changed","retryable":false,"suggestion":""}}
```

`suggestion` and context fields are optional. Graph kernel codes use lower case
(for example `revision_conflict`) and can include `expected_revision` and
`current_revision` directly in `detail`. Request validation uses FastAPI's array:

```json
{"detail":[{"loc":["body","expected_revision"],"msg":"Field required","type":"missing"}]}
```

Read either shape. Use HTTP status plus `detail.code` when it exists; do not
assume every `422` is a Pydantic array or every error code is upper case.
`retryable:true` describes a returned error, not blanket permission to repeat
state-changing requests with new IDs. Endpoint sections list their known domain
statuses. Unexpected handler/persistence/transport failures can return structured
`500 INTERNAL_ERROR`; documentation does not claim to replace runtime failures
with successful values.

## Scientific records and workbench fields

Generic resources are `library`, `experiments`, `datasets`, `ideas`, `theories`,
`claims`, `figures`, `analyses` and `reviews`. They share id/project_id/title/
revision/status/data/created_at/updated_at. Generic PATCH **shallow-merges** data;
an included nested value replaces that value. Generic POST creates metadata,
not execution. POST `/reviews` instead queues review work; there is no generic
review-create operation. Deleting a resource does not remove all its artifact
files. POST scientific actions return runs whose outputs create/update records.

### Figures and statistical presentation

`FigureRecord.data` separates `kind`, `run_ids`/`source_run_ids`, `metric`,
`purpose`, `caption`, `style`, actual `data`, `code`/`code_origin`, `outputs` and
`visual_selection`/`visual_review_status`. Preserve the complete object during
editing. The display style/code is not the statistical source data. `data` can
be a method topology/scene, per-object calibration predictions, or identified
statistical observations. The renderer chooses the applicable source and keeps
the result paths. The `outputs` object is a map of available export keys to
project-relative files; absent outputs are not downloadable formats.

A method `data` object retains supplied `nodes`/`edges`, optional `storyboard`/
`story_context`, and editable `production_scene`/`production_composition` plus
their source contracts and assets. Native scene objects include panels, objects,
connections and annotations. Pixel generation is a conceptual illustration path;
statistical plots/tables stay grounded in actual observations. Rendering does
not turn a schematic into measured evidence.

Figure revisions use `instruction` (singular), optional normalized
`region:{x,y,width,height}`, and the Figure resource revision. A selection made
against an older render must be refreshed before applying its revision request.
Image candidate reuse refers to a saved terminal figure run and real iteration
manifest; it is not a new invented image list. `visual_selection` can be null;
when present it identifies a candidate, complete ranking, reviewers and optional
placement. A whole-image raster completion can report `quality_status` and
`publication_gate_passed`; do not hide these behind a generic success label.

The statistical workflow preserves dataset/method/metric/condition/checkpoint
and independent seed/unit identities. Matching experiment declarations do not
establish source-bound verification. `comparison_eligible`, verification status
and numerical coverage stay distinct in trial displays. Paired inference is
computed by a queued analysis task over explicit real-data columns. Field units,
uncertainty definitions and publication table/curve output details are in
[statistical presentation](STATISTICAL_PRESENTATION.md).

### Manuscripts, layout and review proposals

The paper API accepts a PaperDocument ID or a project ID. GET can initialize the
first draft. `PaperRecord.data.source` and `bibtex` are the editable source;
`bindings`, `numeric_bindings`, `source_run_ids` and `figure_bindings` connect
scientific content to real evidence/artifacts. `pdf_path`/`source_dir` point to
actual files. `compiled_revision` must equal the enclosing paper revision for
current PDF export. Editing source invalidates `layout_plan`/`layout_preflight`,
which can be null. A previous PDF is not automatically the current draft.

The existing layout fields are columns, significant_digits, scientific_notation,
table_font_pt, min_font_pt, max_table_rows, float_placement, figure_span and
table_span. The component below gives their current values/bounds; the normalized
layout rejects unknown keys. `article` supports one/two columns; `iclr2027` uses
the template's single-column layout. Preserve layout decisions, visual placement
anchors and local references rather than regenerating a paper for UI changes.

Generated work can be automatically installed only when its saved paper
snapshot still matches and the manuscript has not been manually edited.
Otherwise a `ReviewRecord` with `kind:paper_generation` stores the entire
proposed bundle, diff and queued snapshot. Accepting it requires `indices:[0]`
and the current PaperDocument revision. Ordinary revision reviews contain exact
original/replacement spans; each original must occur once. A writing proposal
with `replacement:null` needs an evidence judgment and must not be applied as
deletion. Scientific review, literal writing review and compilation are distinct
operations.

### Repository and verification inputs

Repository input accepts **only** `url`, `ref`, `directory`, `transport` and
`credential`. It supports uncredentialed github.com HTTPS or git SSH URLs.
`ref` defaults HEAD, directory defaults source and transport defaults auto.
`credential` is a named worker profile, never an inline token, SSH key or host
credential path. SSH preparation requires a worker-side identity/known_hosts
profile. Submodule/depth/LFS fields are not accepted by the existing input.
Clone submission queues worker work; `/runs/{id}/repository` reports null until
the real provenance manifest exists. See [repository inputs](GITHUB_REPOSITORIES.md).

Verification submission accepts project_id, optional verification node/request
ID and the declared verification task configuration. Source identity/binding/
contract/verdict fields are service-owned. Producer verification views aggregate
current linked verifiers; changed files/configuration/node versions can revoke
prior acceptance. A completed run with `verification_status:unverified` is not
an accepted independent handoff. See [research workflow](RESEARCH_WORKFLOW.md).

### Protocol and publication fields

Base protocol validation requires research_question, hypothesis, baseline,
candidate, metric, statistical_unit, split_policy, selection_policy, decision_rule,
argumentative_duty, meaningful_effect and phase. Confirmatory phase requires
test_used_for_selection:false. A full_submission design also requires target_venue,
dataset_scale_rationale, baseline_selection_rationale, replicate_justification,
fair_compute_policy, leakage_checks, uncertainty_analysis, multiplicity_policy,
confirmation_policy, datasets/baselines/ablations with distinct IDs, distinct
integer seeds, studies covering all four argumentative duties and distinct
accepted_source_ids. The current editable profile determines coverage counts.
Structural validation does not run experiments or verify literature/claims.

Publication audit preserves ready/status/gaps, count/target/remaining-cell data
when available, and current independent verification. operational profile may
have no submission matrix. Route-health signals cover actual recorded repetition;
semantic scientific direction belongs to the queued full-history route review.
These views should stay independently inspectable after the UI is redesigned.

## Events and terminal transport

SSE endpoint `/api/projects/{id}/events` returns `text/event-stream`. It starts
with `event: connected` and `data: {}`. Persisted events use an integer sequence
as `id`, their saved type as the named event, and JSON `data`. Replay later
sequences with the `Last-Event-ID` header; invalid strings fall back to zero.
If the requested cursor predates retained history or is ahead of the latest
saved event, the stream emits a synthetic
`cursor_reset` control event with `id` set to `resume_after_sequence` and JSON
fields `requested_after_sequence`, `oldest_available_sequence`,
`latest_available_sequence`, and `resume_after_sequence`. Reread authoritative
REST state after this event; the stream then continues after the resume cursor.
With no saved events, an ahead cursor resets to checkpoint `0`.
Heartbeats are SSE comments, not
data events. Use EventSource with same-origin
cookies or a fetch-based streaming client capable of setting authentication and
cursor headers. Do not put owner tokens into query strings.

Terminal WebSocket `/ws/projects/{id}/terminal` authenticates with an owner
**cookie** or a real loopback peer; its handler does not read bearer headers.
A supplied Origin must be trusted or have the request hostname. Incoming/outgoing
messages are raw UTF-8 PTY text. Exact controls are
`{"resize":{"rows":40,"cols":120}}` (rows/cols clamped 1..1000) and
`{"action":"close"}`. The resize dispatcher uses the exact leading
`{"resize":`; preserve compact serialization. A reconnect can replay up to
131072 output characters. Session key is project/main branch; idle disconnected
sessions expire after approximately 1800 seconds. Close 1008 means auth/origin/
project rejection; 1013 means all 32 sessions are occupied. Terminal output is
separate from run stdout, process diagnostics and authoritative saved run state.

## Complete HTTP operation reference

Request schemas below retain the existing FastAPI/Pydantic rules for typed
bodies. Named schemas for untyped dictionaries describe intended successful
payloads and known keys; they do not add runtime validators. Optional-body
defaults and path/query defaults come from the actual registered route.

### Authentication

#### `POST /api/auth/login`

Compare body.token with the configured/generated owner token and set the forest_owner HttpOnly, SameSite=Strict cookie for 14 days. HTTPS sets Secure. A supplied Origin must have the same hostname as the request. The token is never returned.

Body: `application/json`: `AuthLoginRequest`; required.

Success `200`: `application/json`: `Authenticated`.

Known domain failures: `401` INVALID_TOKEN; `403` ORIGIN_DENIED.

#### `GET /api/auth/status`

Return whether the cookie or exact Authorization: Bearer <owner-token> matches. This public endpoint does not itself grant the trusted-loopback owner cookie.

Success `200`: `application/json`: `Authenticated`.

### Configuration

#### `GET /api/agents`

Return a bare array of persisted Agent definition records.

Success `200`: `application/json`: `Agent[]`.

#### `POST /api/agents`

Store name, role, instructions, optional provider, tools, config and enabled. Explicit Agent configuration records customized tools so default upgrades do not overwrite them.

Body: `application/json`: `AgentCreate`; required.

Success `200`: `application/json`: `Agent`.

#### `PATCH /api/agents/{ident}`

Replace supplied known fields; config replaces the object. Custom tools remain marked customized. No revision guard.

| Parameter | Location | Type | Required | Default / description |
| --- | --- | --- | --- | --- |
| `ident` | path | string | yes |  |

Body: `application/json`: `AgentWrite`; required.

Success `200`: `application/json`: `Agent`.

Known domain failures: `404` NOT_FOUND.

#### `GET /api/hosts`

Return a bare array of persisted execution host records.

Success `200`: `application/json`: `Host[]`.

#### `POST /api/hosts`

Store name, kind and extensible execution configuration. This does not connect to the host; test it separately.

Body: `application/json`: `HostCreate`; required.

Success `200`: `application/json`: `Host`.

#### `PATCH /api/hosts/{ident}`

Replace supplied name/kind/config and mark the connection untested.

| Parameter | Location | Type | Required | Default / description |
| --- | --- | --- | --- | --- |
| `ident` | path | string | yes |  |

Body: `application/json`: `HostWrite`; required.

Success `200`: `application/json`: `Host`.

Known domain failures: `404` NOT_FOUND.

#### `POST /api/hosts/{ident}/test`

Local success is {connected:true,platform}; remote success is the RemoteRunner test result. Persist connected or failed. No experiment is launched.

| Parameter | Location | Type | Required | Default / description |
| --- | --- | --- | --- | --- |
| `ident` | path | string | yes |  |

Success `200`: `application/json`: `object`.

Known domain failures: `404` NOT_FOUND; `502` HOST_CONNECTION_FAILED.

#### `GET /api/providers`

Return a bare array of persisted provider records.

Success `200`: `application/json`: `Provider[]`.

#### `POST /api/providers`

Accept ollama, openai-compatible or codex_cli kind and an HTTP(S) base_url. Codex CLI requires a localhost placeholder and the host's existing CLI login. Store a supplied api_key outside the returned provider; return has_key. Name/base_url/model are needed to create a usable record.

Body: `application/json`: `ProviderCreate`; required.

Success `200`: `application/json`: `Provider`.

Known domain failures: `400` INVALID_PROVIDER or INVALID_URL.

#### `PATCH /api/providers/{ident}`

Replace supplied known fields; nonempty api_key updates the stored key. Empty/null api_key does not clear it. Mark status untested. This patch does not repeat create-time kind/URL validation.

| Parameter | Location | Type | Required | Default / description |
| --- | --- | --- | --- | --- |
| `ident` | path | string | yes |  |

Body: `application/json`: `ProviderWrite`; required.

Success `200`: `application/json`: `Provider`.

Known domain failures: `404` NOT_FOUND.

#### `GET /api/providers/{ident}/models`

Return the provider response unchanged: Ollama normally uses {models:[...]}; OpenAI-compatible endpoints normally use {data:[...]}. Other JSON shapes remain provider-defined. This is a transport/list request, not a chat completion. Do not assume a normalized bare array.

| Parameter | Location | Type | Required | Default / description |
| --- | --- | --- | --- | --- |
| `ident` | path | string | yes |  |

Success `200`: `application/json`: `JsonValue`.

Known domain failures: `404` NOT_FOUND.

#### `GET /api/providers/{ident}/references`

List project, node, Agent, reporting and global-default references, plus the count of preserved historical model requests.

| Parameter | Location | Type | Required | Default / description |
| --- | --- | --- | --- | --- |
| `ident` | path | string | yes |  |

Success `200`: `application/json`: `object`.

Known domain failures: `404` NOT_FOUND.

#### `POST /api/providers/{ident}/restore`

Mark a retired provider untested so it can be edited and used again. Credentials removed during retirement must be configured again.

| Parameter | Location | Type | Required | Default / description |
| --- | --- | --- | --- | --- |
| `ident` | path | string | yes |  |

Success `200`: `application/json`: `Provider`.

Known domain failures: `404` NOT_FOUND.

#### `POST /api/providers/{ident}/retire`

Replace or clear every active reference, remove the stored credential, and mark the provider retired while preserving historical model request records. replacement_provider_id may be null to clear references.

| Parameter | Location | Type | Required | Default / description |
| --- | --- | --- | --- | --- |
| `ident` | path | string | yes |  |

Body: `application/json`: `JsonObject`; optional.

Success `200`: `application/json`: `JsonValue`.

Known domain failures: `404` NOT_FOUND; `422` INVALID_REPLACEMENT.

#### `POST /api/providers/{ident}/test`

Send a short JSON-status prompt through ModelClient. This DOES perform a model call and can consume tokens/cost. Persist connected/failed; status is the connection outcome, while request_status preserves the ModelClient request state when available. Other ModelClient result fields are extensible.

| Parameter | Location | Type | Required | Default / description |
| --- | --- | --- | --- | --- |
| `ident` | path | string | yes |  |

Success `200`: `application/json`: `object`.

Known domain failures: `404` NOT_FOUND; `502` PROVIDER_CONNECTION_FAILED (retryable).

#### `GET /api/providers/{ident}/usage`

Read configured-rate estimates and reservations across all projects using this provider. requests contains the 100 most recent records; request_count is the full count. Missing limit/remaining/individual estimate values are null, not zero.

| Parameter | Location | Type | Required | Default / description |
| --- | --- | --- | --- | --- |
| `ident` | path | string | yes |  |

Success `200`: `application/json`: `ProviderUsage`.

Known domain failures: `404` NOT_FOUND.

#### `GET /api/settings`

Return the saved extensible preference object, or language=en, theme=light, retention_days=30 and default_mode=assisted when no settings row exists.

Success `200`: `application/json`: `JsonObject`.

#### `PATCH /api/settings`

Shallow-merge arbitrary preference keys. No revision guard; nested values replace their previous values.

Body: `application/json`: `JsonObject`; required.

Success `200`: `application/json`: `JsonObject`.

#### `POST /api/settings/cleanup`

Delete terminal runs whose finished_at is older than the days cutoff and their output directories; mark dependent material stale. project_id optionally restricts run deletion. clear_edit_history applies to every project's graph history even when project_id is supplied.

Body: `application/json`: `CleanupRequest`; required.

Success `200`: `application/json`: `CleanupResult`.

### Events

#### `GET /api/projects/{ident}/events`

Server-Sent Events, not JSON. Begin with event: connected and data: {}. Persisted events include id: sequence, event: event.type and JSON data: event.data. Reconnect with Last-Event-ID to replay later sequences in batches of 100. If retained history starts after the requested cursor or the cursor exceeds the latest saved event, emit cursor_reset with id: resume_after_sequence and JSON requested_after_sequence, oldest_available_sequence, latest_available_sequence and resume_after_sequence; reread authoritative REST state, then continue from that ID. With no saved events, an ahead cursor resets to checkpoint 0. Invalid/missing cursor starts at zero and can also require this reset. Send a comment heartbeat roughly once per second; the stream ends on disconnect. Headers Cache-Control:no-cache and X-Accel-Buffering:no.

| Parameter | Location | Type | Required | Default / description |
| --- | --- | --- | --- | --- |
| `ident` | path | string | yes |  |
| `Last-Event-ID` | header | string | no | Last delivered integer sequence, encoded as a string. Missing/invalid values replay from zero; if retained events begin later or the cursor is ahead of the latest event, the stream emits cursor_reset and a resynchronization cursor. |

Success `200`: `text/event-stream`: `string`.

Known domain failures: `404` NOT_FOUND.

### Experiments

#### `GET /api/data/{ident}/rows`

ident resolves a TaskRun first, then an Analysis record (not a DatasetAsset ID). path defaults to run.output_path/predictions.csv or analysis.data.path. CSV/Parquet is fully read; filter is literal substring across cells and sort is an existing column. Return columns/selected rows/filtered total/origin. offset is clamped at zero; limit is capped at 1000. For bounded uploaded-table previews use /projects/{ident}/file/preview.

| Parameter | Location | Type | Required | Default / description |
| --- | --- | --- | --- | --- |
| `ident` | path | string | yes |  |
| `path` | query | string or null | no |  |
| `offset` | query | integer | no | Default `0`.  |
| `limit` | query | integer | no | Default `100`.  |
| `filter` | query | string | no | Default ``.  |
| `sort` | query | string | no | Default ``.  |
| `descending` | query | boolean | no | Default `False`.  |

Success `200`: `application/json`: `DataRows`.

Known domain failures: `403` PATH_ESCAPE; `404` NOT_FOUND or MISSING_ARTIFACT.

#### `POST /api/experiments/compare`

Require unique nonempty run_ids from one project; objective is optional. Check dataset/version, split, evaluation, metric, statistical unit, fair scientific budget and configured fields. Noncompleted/missing declarations are unverified; conflicting scopes are incomparable. Matching declarations are not independent evidence verification. config.provider_snapshot is omitted from compared rows.

Body: `application/json`: `ExperimentCompareRequest`; required.

Success `200`: `application/json`: `ExperimentComparison`.

Known domain failures: `400` CROSS_PROJECT; `404` NOT_FOUND; `422` INVALID_COMPARISON.

#### `POST /api/experiments/{ident}/launch`

Merge saved experiment data with the optional body, bind experiment_id/revision and optional associated node, and return Run. Body fields are experiment configuration overrides.

| Parameter | Location | Type | Required | Default / description |
| --- | --- | --- | --- | --- |
| `ident` | path | string | yes |  |

Body: `application/json`: `JsonObject`; optional.

Success `200`: `application/json`: `Run`.

Known domain failures: `400` Scheduling/input errors; `404` NOT_FOUND.

#### `POST /api/projects/{ident}/protocol/validate`

Require the base protocol judgment/unit/selection/threshold fields. Confirmatory phase requires test_used_for_selection:false. A full_submission project additionally requires complete dataset/baseline/ablation/seed/study/accepted-source design and rationale fields. Return structurally_checked protocol/profile/scope; no experiment executes.

| Parameter | Location | Type | Required | Default / description |
| --- | --- | --- | --- | --- |
| `ident` | path | string | yes |  |

Body: `application/json`: `ProtocolRequest`; required.

Success `200`: `application/json`: `ProtocolResult`.

Known domain failures: `404` NOT_FOUND; `422` INVALID_PROTOCOL.

#### `POST /api/projects/{ident}/statistics/paired`

Require project-relative existing path and unit_column/baseline_column/candidate_column. Worker analysis_type=paired uses independent unit clusters; optional direction lower/higher, confidence, bootstrap_samples, seed and meaningful_effect configure the analysis. When required policy or an explicit verifier guard applies, path must select the uniquely admitted source-bound checked artifact or its verified graph input copy. Optional unguarded requests remain available. Return Run, not a confidence interval immediately.

| Parameter | Location | Type | Required | Default / description |
| --- | --- | --- | --- | --- |
| `ident` | path | string | yes |  |

Body: `application/json`: `PairedStatisticsRequest`; required.

Success `200`: `application/json`: `Run`.

Known domain failures: `403` PATH_ESCAPE; `404` NOT_FOUND or MISSING_ARTIFACT; `422` MISSING_FIELD.

#### `POST /api/projects/{ident}/statistics/review`

Merge the extensible body, force review_scope=statistics and return queued Run. Optional request_id controls scheduler reuse; review findings arrive through saved run/resource output.

| Parameter | Location | Type | Required | Default / description |
| --- | --- | --- | --- | --- |
| `ident` | path | string | yes |  |

Body: `application/json`: `JsonObject`; optional.

Success `200`: `application/json`: `Run`.

Known domain failures: `404` NOT_FOUND.

### Figures

#### `GET /api/figures/{ident}/export`

Query format defaults svg and indexes figure.data.outputs; available keys depend on the actual render. Return file bytes with filename figure.<format>. Missing output returns 409; do not infer that every figure supports every common format.

| Parameter | Location | Type | Required | Default / description |
| --- | --- | --- | --- | --- |
| `ident` | path | string | yes |  |
| `format` | query | string | no | Default `svg`.  |

Success `200`: `image/svg+xml`: `string`; `image/png`: `string`; `application/pdf`: `string`; `application/octet-stream`: `string`.

Known domain failures: `403` PATH_ESCAPE; `404` NOT_FOUND or MISSING_ARTIFACT; `409` ARTIFACT_UNAVAILABLE.

#### `POST /api/figures/{ident}/render`

Capture figure_id/revision/data plus body overrides and return a queued Run. Optional body request_id/configuration is passed to the figure worker; outputs are available only after the task completes and its resource is updated.

| Parameter | Location | Type | Required | Default / description |
| --- | --- | --- | --- | --- |
| `ident` | path | string | yes |  |

Body: `application/json`: `FigureTaskRequest`; optional.

Success `200`: `application/json`: `Run`.

Known domain failures: `404` NOT_FOUND.

#### `POST /api/figures/{ident}/revise`

Optional expected_revision compares the Figure resource revision, default current. Pass instruction (singular), optional region and other figure task parameters in the extensible body. Return queued Run; it is not the revised image itself.

| Parameter | Location | Type | Required | Default / description |
| --- | --- | --- | --- | --- |
| `ident` | path | string | yes |  |

Body: `application/json`: `FigureTaskRequest`; required.

Success `200`: `application/json`: `Run`.

Known domain failures: `404` NOT_FOUND; `409` REVISION_CONFLICT.

### Files

#### `POST /api/projects/import`

multipart/form-data file with forest-project-v1 manifest. Validate graph and archive paths, reject symlinks/path traversal, more than `max_project_archive_members` ZIP entries (default `10000`, configured by `FOREST_MAX_PROJECT_ARCHIVE_MEMBERS`), and >500 MiB decompressed archives. The member count includes directories and entries outside `files/`; rejection returns `413 ARCHIVE_TOO_LARGE` before manifest access or project creation. The configured limit must be a positive integer. Create a new project with remapped graph/resource identifiers; preserve its selected provider when that ID exists in the current workspace. If the saved provider is unavailable, mark provider_selection_required and prevent provider-backed runs from silently falling back until the owner edits project settings. Active imported work is interrupted and execution is not replayed.

Body: `multipart/form-data`: `Body_import_project_api_projects_import_post`; required.

Success `200`: `application/json`: `Project`.

Known domain failures: `400` INVALID_ARCHIVE, UNSAFE_ARCHIVE or graph validation errors; `413` UPLOAD_TOO_LARGE or ARCHIVE_TOO_LARGE.

#### `GET /api/projects/{ident}/download`

FileResponse with inline Content-Disposition and the file's basename. Content type depends on its filename; use the response bytes/blob, not JSON. No text-size limit is applied here.

| Parameter | Location | Type | Required | Default / description |
| --- | --- | --- | --- | --- |
| `ident` | path | string | yes |  |
| `path` | query | string | yes |  |

Success `200`: `application/octet-stream`: `string`; `application/pdf`: `string`; `image/svg+xml`: `string`; `image/png`: `string`; `text/plain`: `string`.

Known domain failures: `400` NOT_FILE; `403` PATH_ESCAPE; `404` NOT_FOUND or MISSING_ARTIFACT.

#### `POST /api/projects/{ident}/export`

ZIP contains forest-project.json and eligible workspace files. Optional paths selects included file prefixes, not a smaller graph/resource/run manifest. Export omits private graph history and machine fields while preserving the nonsecret project provider ID; credentials remain excluded. An included ordinary file above 100 MiB is rejected with a structured 413 EXPORT_FILE_TOO_LARGE error naming its relative path; symlinks and hidden paths are skipped. Do not treat the archive as an encrypted secret store.

| Parameter | Location | Type | Required | Default / description |
| --- | --- | --- | --- | --- |
| `ident` | path | string | yes |  |

Body: `application/json`: `ProjectExportRequest`; optional.

Success `200`: `application/zip`: `string`.

Known domain failures: `404` NOT_FOUND; `413` EXPORT_FILE_TOO_LARGE.

#### `DELETE /api/projects/{ident}/file`

Delete the selected file or directory recursively and mark consumers stale; removing the project root is rejected. Advance and retain file revision generations for removed paths so stale editor tokens conflict if a path is recreated. No expected_revision guard.

| Parameter | Location | Type | Required | Default / description |
| --- | --- | --- | --- | --- |
| `ident` | path | string | yes |  |
| `path` | query | string | yes |  |

Success `200`: `application/json`: `Deleted`.

Known domain failures: `400` INVALID_PATH; `403` PATH_ESCAPE; `404` NOT_FOUND or MISSING_ARTIFACT.

#### `GET /api/projects/{ident}/file`

Return text, file revision and origin from one snapshot serialized with owner uploads, edits, moves and deletes. Paths are normalized relative to the project root. Binary files need download/preview. The file revision is distinct from project, node and resource revisions. The default revision/origin for executor/import files is 0/executor_or_import.

| Parameter | Location | Type | Required | Default / description |
| --- | --- | --- | --- | --- |
| `ident` | path | string | yes |  |
| `path` | query | string | yes |  |

Success `200`: `application/json`: `FileRead`.

Known domain failures: `400` NOT_FILE; `403` PATH_ESCAPE; `404` NOT_FOUND or MISSING_ARTIFACT; `413` FILE_TOO_LARGE (>10000000 bytes); `415` BINARY_FILE.

#### `PUT /api/projects/{ident}/file`

FileWrite contains path/content, optional FILE expected_revision and create_only (default false). With create_only:true, reject an occupied path with FILE_EXISTS while holding the publication lock, preserving its bytes and revision. Otherwise atomically replace file content, increment its revision, synchronize working manuscript files and mark consuming records stale. A first write to a missing path may use expected_revision=0; after deletion it advances that path's retained revision generation. Omitting expected_revision accepts the current revision.

| Parameter | Location | Type | Required | Default / description |
| --- | --- | --- | --- | --- |
| `ident` | path | string | yes |  |

Body: `application/json`: `FileWrite`; required.

Success `200`: `application/json`: `FileWritten`.

Known domain failures: `403` PATH_ESCAPE; `404` NOT_FOUND; `409` FILE_EXISTS, REVISION_CONFLICT or WORKSPACE_PENDING.

#### `GET /api/projects/{ident}/file/preview`

CSV/TSV/Parquet preview: offset 0..10000000, limit 1..500. total describes the complete source table; at most 100 displayed columns, at most 1000 source columns, bounded cell lengths/nesting. Report truncation fields instead of implying the excerpt is complete. File size and Parquet decoded row group limit are 128 MiB. Source changes during read return retryable TABLE_CHANGED.

| Parameter | Location | Type | Required | Default / description |
| --- | --- | --- | --- | --- |
| `ident` | path | string | yes |  |
| `path` | query | string | yes |  |
| `offset` | query | integer | no | Default `0`.  minimum=0, maximum=10000000. |
| `limit` | query | integer | no | Default `100`.  minimum=1, maximum=500. |

Success `200`: `application/json`: `TablePreview`.

Known domain failures: `400` NOT_FILE; `403` PATH_ESCAPE; `404` NOT_FOUND or MISSING_ARTIFACT; `409` TABLE_CHANGED; `413` TABLE_TOO_LARGE, TABLE_TOO_WIDE or TABLE_ROW_GROUP_TOO_LARGE; `415` UNSUPPORTED_TABLE_FORMAT; `422` TABLE_PARSE_FAILED; `503` PARQUET_ENGINE_UNAVAILABLE.

#### `POST /api/projects/{ident}/file/rename`

Use path and new_path, both within the project root. Create destination parents, reject an existing destination and mark consumers of the old path stale. Move file revision rows to the new paths and increment their revisions.

| Parameter | Location | Type | Required | Default / description |
| --- | --- | --- | --- | --- |
| `ident` | path | string | yes |  |

Body: `application/json`: `FileRenameRequest`; required.

Success `200`: `application/json`: `FilePath`.

Known domain failures: `403` PATH_ESCAPE; `404` NOT_FOUND or MISSING_ARTIFACT; `409` FILE_EXISTS.

#### `GET /api/projects/{ident}/files`

Return a bounded page of eligible workspace entries in depth-first filesystem traversal order. Pass the opaque next_cursor to continue; has_more indicates that entries remain. Cursors are process-local and expire after 10 minutes; on expiration, restart from the first page. Skip symlinks and hidden path components except .forest-bases. Paths are relative to the project root and modified is a filesystem epoch time.

| Parameter | Location | Type | Required | Default / description |
| --- | --- | --- | --- | --- |
| `ident` | path | string | yes |  |
| `limit` | query | integer | no | Default `500`.  minimum=1, maximum=1000. |
| `cursor` | query | string or null | no |  |

Success `200`: `application/json`: `FileList`.

Known domain failures: `404` NOT_FOUND; `409` FILE_CURSOR_EXPIRED; `422` INVALID_FILE_CURSOR.

#### `POST /api/projects/{ident}/files/existing`

Return existing files and directories for supplied relative paths in the project workspace. Accepts at most 1,000 paths per request so multi-file uploads can confirm replacements before publishing any selected file; directories cannot be replaced by file uploads.

| Parameter | Location | Type | Required | Default / description |
| --- | --- | --- | --- | --- |
| `ident` | path | string | yes |  |

Body: `application/json`: `FileExistenceRequest`; required.

Success `200`: `application/json`: `FileExistenceResult`.

Known domain failures: `403` PATH_ESCAPE; `404` NOT_FOUND; `422` INVALID_FILE_PATHS.

#### `POST /api/projects/{ident}/upload`

multipart/form-data field file; directory is a QUERY parameter, default uploads. Only the uploaded filename basename is used. Publish the file, increment its file revision (including identical-byte replacements), set user_import origin, mark consumers stale and return path/size/origin. Older editor expected_revision values then return REVISION_CONFLICT. Exceeding configured max_upload_mb preserves an existing destination and removes only the temporary file.

| Parameter | Location | Type | Required | Default / description |
| --- | --- | --- | --- | --- |
| `ident` | path | string | yes |  |
| `directory` | query | string | no | Default `uploads`.  |
| `overwrite` | query | boolean | no | Default `True`.  |

Body: `multipart/form-data`: `Body_upload_api_projects__ident__upload_post`; required.

Success `200`: `application/json`: `FileUpload`.

Known domain failures: `403` PATH_ESCAPE; `404` NOT_FOUND; `409` WORKSPACE_PENDING; `413` UPLOAD_TOO_LARGE.

### Graph

#### `GET /api/branches/compare`

Require left and right branch IDs in the same project. Return the BranchWorkspace file/config comparison plus full left/right branch records and the combined run list.

| Parameter | Location | Type | Required | Default / description |
| --- | --- | --- | --- | --- |
| `left` | query | string | yes |  |
| `right` | query | string | yes |  |

Success `200`: `application/json`: `object`.

Known domain failures: `400` CROSS_PROJECT; `404` NOT_FOUND.

#### `GET /api/nodes/{ident}`

Return a node with flattened kernel/runtime metadata. Its revision is distinct from the containing project's graph revision.

| Parameter | Location | Type | Required | Default / description |
| --- | --- | --- | --- | --- |
| `ident` | path | string | yes |  |

Success `200`: `application/json`: `ResearchNode`.

Known domain failures: `404` NOT_FOUND.

#### `PATCH /api/nodes/{ident}`

Translate the body into an edit_node graph command with a server-created request ID. expected_revision is the PROJECT graph revision; omission reads the current revision. Return GraphCommandResult, not a node. Editable nested node objects use the kernel's deep merge.

| Parameter | Location | Type | Required | Default / description |
| --- | --- | --- | --- | --- |
| `ident` | path | string | yes |  |

Body: `application/json`: `NodePatch`; required.

Success `200`: `application/json`: `GraphCommandResult`.

Known domain failures: `400` invalid_patch; `404` NOT_FOUND; `409` revision_conflict.

#### `GET /api/nodes/{ident}/context`

Build a role-aware preview from current graph/goal/budget, declared input materials and context overrides. Material previews can be truncated; retrievable_materials and omitted describe the full-source lookup opportunities. Capacity is a soft preview hint, not an execution result.

| Parameter | Location | Type | Required | Default / description |
| --- | --- | --- | --- | --- |
| `ident` | path | string | yes |  |

Success `200`: `application/json`: `ContextSnapshot`.

Known domain failures: `404` NOT_FOUND.

#### `POST /api/nodes/{ident}/context/rebuild`

When expected_revision is supplied, require it to match the current project revision. Merge the remaining extensible body into node.context_overrides, force needs_refresh:false, emit context_changed and return a fresh ContextSnapshot.

| Parameter | Location | Type | Required | Default / description |
| --- | --- | --- | --- | --- |
| `ident` | path | string | yes |  |

Body: `application/json`: `JsonObject`; optional.

Success `200`: `application/json`: `ContextSnapshot`.

Known domain failures: `404` NOT_FOUND; `409` REVISION_CONFLICT.

#### `GET /api/projects/{ident}/graph`

Return nodes, typed edges, branches, project revision, goal, budget and additional public graph metadata. Exclude _history.

| Parameter | Location | Type | Required | Default / description |
| --- | --- | --- | --- | --- |
| `ident` | path | string | yes |  |

Success `200`: `application/json`: `GraphState`.

Known domain failures: `404` NOT_FOUND.

#### `POST /api/projects/{ident}/graph/batch`

Require current PROJECT expected_revision and between 1 and 200 commands per request. Supports add_node/edit_node/add_dependency/remove_dependency/prune_branch/restore_branch/set_main_branch; one edit_node may update selected node positions atomically with params.positions keyed by target ID. Workspace fork/merge and other operations require separate graph commands. Optional request_id shares project command-receipt scope. One undo snapshot is saved; result counts and final graph revision are returned, not the graph itself.

| Parameter | Location | Type | Required | Default / description |
| --- | --- | --- | --- | --- |
| `ident` | path | string | yes |  |

Body: `application/json`: `GraphBatchRequest`; required.

Success `200`: `application/json`: `GraphBatchResult`.

Known domain failures: `404` NOT_FOUND; `409` REVISION_CONFLICT; `422` INVALID_COMMANDS or UNSUPPORTED_BATCH_OPERATION.

#### `POST /api/projects/{ident}/graph/commands`

Apply one kernel command under the project writer transaction. The project request_id deduplicates saved command receipts before revision comparison; reuse it only for the same logical submission. Return public graph, impact, run_nodes and run_ids. run:true enqueues selected affected nodes. Preview conflicts/missing inputs must be inspected; undo restores graph content, not completed external effects. Invalid node patch, dependency endpoint, fork node-ID list or split-part shapes are rejected before execution with invalid_command and the offending field.

| Parameter | Location | Type | Required | Default / description |
| --- | --- | --- | --- | --- |
| `ident` | path | string | yes |  |

Body: `application/json`: `GraphCommand`; required.

Success `200`: `application/json`: `GraphCommandResult`.

Known domain failures: `400` Graph command errors; `404` NOT_FOUND or missing_node; `409` revision_conflict or patch_conflict; `422` CROSS_PROJECT or invalid_command.

#### `GET /api/projects/{ident}/graph/page`

Filter optional branch_id and case-insensitive title query; order created_at/id. Clamp offset>=0 and limit 1..500 for selection, but echo the original offset. edges includes any edge incident to a displayed node, so its other endpoint may be outside this page. next_offset is null on a short page; a full final page may require one empty follow-up.

| Parameter | Location | Type | Required | Default / description |
| --- | --- | --- | --- | --- |
| `ident` | path | string | yes |  |
| `offset` | query | integer | no | Default `0`.  |
| `limit` | query | integer | no | Default `100`.  |
| `branch_id` | query | string or null | no |  |
| `query` | query | string | no | Default ``.  |

Success `200`: `application/json`: `GraphPage`.

Known domain failures: `404` NOT_FOUND.

#### `POST /api/projects/{ident}/graph/preview`

Require the current expected_revision. Return only GraphImpact, not a graph or receipt. Preview writes no graph/workspace changes. body.project_id, when set, must match the path project. Invalid node patch, dependency endpoint, fork node-ID list or split-part shapes are rejected before execution with invalid_command and the offending field.

| Parameter | Location | Type | Required | Default / description |
| --- | --- | --- | --- | --- |
| `ident` | path | string | yes |  |

Body: `application/json`: `GraphCommand`; required.

Success `200`: `application/json`: `GraphImpact`.

Known domain failures: `400` Graph command errors; `404` NOT_FOUND or missing_node; `409` revision_conflict or patch_conflict; `422` CROSS_PROJECT or invalid_command.

### Interventions

#### `POST /api/decisions/{ident}/answer`

Owner-only durable intent. Reviewed revisions and exact action identities are enforced; a stale action is marked stale and discarded rather than approved. Accepted is distinct from applied. Stop effects are reconciled outside graph transactions; uncertain effects remain visible and retryable. Instruction delivery is confirmed by actual prepared request and provider response receipts, not model agreement. Human decisions persist across worker restart and use the ordinary budget-checked resume path.

| Parameter | Location | Type | Required | Default / description |
| --- | --- | --- | --- | --- |
| `ident` | path | string | yes |  |

Body: `application/json`: `DecisionAnswer`; required.

Success `200`: `application/json`: `DecisionView`.

Known domain failures: `404` Target unavailable; `409` Reviewed revision, request identity, decision or run state conflict.

#### `GET /api/interventions/{ident}`

Owner-only durable intent. Reviewed revisions and exact action identities are enforced; a stale action is marked stale and discarded rather than approved. Accepted is distinct from applied. Stop effects are reconciled outside graph transactions; uncertain effects remain visible and retryable. Instruction delivery is confirmed by actual prepared request and provider response receipts, not model agreement. Human decisions persist across worker restart and use the ordinary budget-checked resume path.

| Parameter | Location | Type | Required | Default / description |
| --- | --- | --- | --- | --- |
| `ident` | path | string | yes |  |

Success `200`: `application/json`: `InterventionView`.

Known domain failures: `404` Target unavailable; `409` Reviewed revision, request identity, decision or run state conflict.

#### `GET /api/projects/{ident}/decisions`

Owner-only durable intent. Reviewed revisions and exact action identities are enforced; a stale action is marked stale and discarded rather than approved. Accepted is distinct from applied. Stop effects are reconciled outside graph transactions; uncertain effects remain visible and retryable. Instruction delivery is confirmed by actual prepared request and provider response receipts, not model agreement. Human decisions persist across worker restart and use the ordinary budget-checked resume path.

| Parameter | Location | Type | Required | Default / description |
| --- | --- | --- | --- | --- |
| `ident` | path | string | yes |  |
| `limit` | query | integer | no | Default `50`.  minimum=1, maximum=200. |
| `status` | query | string or null | no |  |

Success `200`: `application/json`: `DecisionView[]`.

Known domain failures: `404` Target unavailable; `409` Reviewed revision, request identity, decision or run state conflict.

#### `POST /api/projects/{ident}/instructions`

Owner-only durable intent. Reviewed revisions and exact action identities are enforced; a stale action is marked stale and discarded rather than approved. Accepted is distinct from applied. Stop effects are reconciled outside graph transactions; uncertain effects remain visible and retryable. Instruction delivery is confirmed by actual prepared request and provider response receipts, not model agreement. Human decisions persist across worker restart and use the ordinary budget-checked resume path.

| Parameter | Location | Type | Required | Default / description |
| --- | --- | --- | --- | --- |
| `ident` | path | string | yes |  |

Body: `application/json`: `InstructionRequest`; required.

Success `200`: `application/json`: `InterventionView`.

Known domain failures: `404` Target unavailable; `409` Reviewed revision, request identity, decision or run state conflict.

#### `GET /api/projects/{ident}/interventions`

Owner-only durable intent. Reviewed revisions and exact action identities are enforced; a stale action is marked stale and discarded rather than approved. Accepted is distinct from applied. Stop effects are reconciled outside graph transactions; uncertain effects remain visible and retryable. Instruction delivery is confirmed by actual prepared request and provider response receipts, not model agreement. Human decisions persist across worker restart and use the ordinary budget-checked resume path. The before timestamp cursor accepts before_id to disambiguate records sharing a timestamp; clients can page through all retained history.

| Parameter | Location | Type | Required | Default / description |
| --- | --- | --- | --- | --- |
| `ident` | path | string | yes |  |
| `limit` | query | integer | no | Default `50`.  minimum=1, maximum=200. |
| `before` | query | string or null | no |  |
| `before_id` | query | string or null | no |  |

Success `200`: `application/json`: `InterventionView[]`.

Known domain failures: `404` Target unavailable; `409` Reviewed revision, request identity, decision or run state conflict.

#### `POST /api/research/proposals/{ident}/reject`

Owner-only durable intent. Reviewed revisions and exact action identities are enforced; a stale action is marked stale and discarded rather than approved. Accepted is distinct from applied. Stop effects are reconciled outside graph transactions; uncertain effects remain visible and retryable. Instruction delivery is confirmed by actual prepared request and provider response receipts, not model agreement. Human decisions persist across worker restart and use the ordinary budget-checked resume path.

| Parameter | Location | Type | Required | Default / description |
| --- | --- | --- | --- | --- |
| `ident` | path | string | yes |  |

Body: `application/json`: `RejectionRequest`; required.

Success `200`: `application/json`: `ResourceRecord`.

Known domain failures: `404` Target unavailable; `409` Reviewed revision, request identity, decision or run state conflict.

#### `GET /api/runs/{ident}/acceptance`

Owner-only durable intent. Reviewed revisions and exact action identities are enforced; a stale action is marked stale and discarded rather than approved. Accepted is distinct from applied. Stop effects are reconciled outside graph transactions; uncertain effects remain visible and retryable. Instruction delivery is confirmed by actual prepared request and provider response receipts, not model agreement. Human decisions persist across worker restart and use the ordinary budget-checked resume path.

| Parameter | Location | Type | Required | Default / description |
| --- | --- | --- | --- | --- |
| `ident` | path | string | yes |  |

Success `200`: `application/json`: `AcceptanceView`.

Known domain failures: `404` Target unavailable; `409` Reviewed revision, request identity, decision or run state conflict.

#### `GET /api/runs/{ident}/applicability`

Owner-only durable intent. Reviewed revisions and exact action identities are enforced; a stale action is marked stale and discarded rather than approved. Accepted is distinct from applied. Stop effects are reconciled outside graph transactions; uncertain effects remain visible and retryable. Instruction delivery is confirmed by actual prepared request and provider response receipts, not model agreement. Human decisions persist across worker restart and use the ordinary budget-checked resume path.

| Parameter | Location | Type | Required | Default / description |
| --- | --- | --- | --- | --- |
| `ident` | path | string | yes |  |

Success `200`: `application/json`: `ApplicabilityView`.

Known domain failures: `404` Target unavailable; `409` Reviewed revision, request identity, decision or run state conflict.

#### `POST /api/runs/{ident}/applicability/decisions`

Owner-only durable intent. Reviewed revisions and exact action identities are enforced; a stale action is marked stale and discarded rather than approved. Accepted is distinct from applied. Stop effects are reconciled outside graph transactions; uncertain effects remain visible and retryable. Instruction delivery is confirmed by actual prepared request and provider response receipts, not model agreement. Human decisions persist across worker restart and use the ordinary budget-checked resume path.

| Parameter | Location | Type | Required | Default / description |
| --- | --- | --- | --- | --- |
| `ident` | path | string | yes |  |

Body: `application/json`: `ApplicabilityDecision`; required.

Success `200`: `application/json`: `InterventionView`.

Known domain failures: `404` Target unavailable; `409` Reviewed revision, request identity, decision or run state conflict.

### Literature

#### `POST /api/browser/read`

Require project_id and url; screenshot is optional (default false). Read the real page, save text/optional screenshot relative paths and a SourcePaper with a page passage. Return the saved RecordItem.

Body: `application/json`: `BrowserReadRequest`; required.

Success `200`: `application/json`: `RecordItem`.

Known domain failures: `404` NOT_FOUND.

#### `POST /api/library/compare`

ids selects sources in one project. Return papers with title/abstract/authors/year/reading_scope/notes and REPORTED label. This compares records, not verified experimental effects.

Body: `application/json`: `IdListRequest`; required.

Success `200`: `application/json`: `LibraryCompareResult`.

Known domain failures: `400` CROSS_PROJECT; `404` NOT_FOUND.

#### `GET /api/library/export-bibtex`

Require query project_id; return application/x-bibtex text with attachment references.bib. Do not parse as JSON.

| Parameter | Location | Type | Required | Default / description |
| --- | --- | --- | --- | --- |
| `project_id` | query | string | yes |  |

Success `200`: `application/x-bibtex`: `string`.

#### `POST /api/library/import`

Supply project_id and an identifier (DOI/arXiv/public PDF) or paper metadata as an object or list. Save records and passages. Within a project, a matching publication reuses its saved record during concurrent imports. Returns one RecordItem or {records:[...]}. Source metadata remains extensible.

Body: `application/json`: `LibraryImportRequest`; required.

Success `200`: `application/json`: `RecordItem or object`.

Known domain failures: `400` MISSING_IDENTIFIER; `404` NOT_FOUND; `422` IMPORT_FAILED.

#### `POST /api/library/search`

query is required; source defaults crossref, limit defaults 8 and is capped at 30. Return {results:[provider-specific metadata]}. This does not import results into a project.

Body: `application/json`: `LibrarySearchRequest`; required.

Success `200`: `application/json`: `LibrarySearchResult`.

Known domain failures: `502` LITERATURE_SEARCH_FAILED (retryable).

#### `GET /api/library/{ident}/passages`

Return a bare array of SourcePassage RecordItems when indexed passage rows exist, otherwise the source data.passages array. Raw fallback passage objects can have page/text/source_url rather than record IDs.

| Parameter | Location | Type | Required | Default / description |
| --- | --- | --- | --- | --- |
| `ident` | path | string | yes |  |

Success `200`: `application/json`: `RecordItem or JsonObject[]`.

Known domain failures: `404` NOT_FOUND.

#### `POST /api/library/{ident}/reindex`

Require data.pdf_path or fulltext_path within the project. Replace data.passages, set reading_scope=fulltext, increment the source resource revision and mark consuming material stale.

| Parameter | Location | Type | Required | Default / description |
| --- | --- | --- | --- | --- |
| `ident` | path | string | yes |  |

Success `200`: `application/json`: `RecordItem`.

Known domain failures: `403` PATH_ESCAPE; `404` NOT_FOUND or MISSING_ARTIFACT; `409` FULLTEXT_UNAVAILABLE.

### Manuscripts

#### `GET /api/papers/{ident}`

ident may be a PaperDocument ID or a project ID. When a project has no manuscript, this GET creates its initial draft. Return PaperRecord; clients must not treat the endpoint as a purely side-effect-free lookup.

| Parameter | Location | Type | Required | Default / description |
| --- | --- | --- | --- | --- |
| `ident` | path | string | yes |  |

Success `200`: `application/json`: `PaperRecord`.

Known domain failures: `404` NOT_FOUND.

#### `PATCH /api/papers/{ident}`

Resolve paper/project ID, optionally compare the PaperDocument revision, shallow-merge body.data and set manual/needs_update state. Increment paper revision; source/bibtex changes clear previous layout checks and write the working source. Return PaperRecord.

| Parameter | Location | Type | Required | Default / description |
| --- | --- | --- | --- | --- |
| `ident` | path | string | yes |  |

Body: `application/json`: `PaperPatch`; required.

Success `200`: `application/json`: `PaperRecord`.

Known domain failures: `404` NOT_FOUND; `409` REVISION_CONFLICT.

#### `POST /api/papers/{ident}/check`

Resolve existing paper/project ID without creating a draft. Return issues/status/coverage/writing_profile. Check citation keys, labels, selected literal defensive phrases, stale bindings and missing materials; this is not an exhaustive scientific semantic review.

| Parameter | Location | Type | Required | Default / description |
| --- | --- | --- | --- | --- |
| `ident` | path | string | yes |  |

Success `200`: `application/json`: `PaperCheck`.

Known domain failures: `404` NOT_FOUND.

#### `POST /api/papers/{ident}/compile`

Resolve paper/project ID, optionally compare PaperDocument revision and capture exact source/bibtex/paper_revision. Return queued Run; this is not a compiled PDF response.

| Parameter | Location | Type | Required | Default / description |
| --- | --- | --- | --- | --- |
| `ident` | path | string | yes |  |

Body: `application/json`: `PaperRevisionRequest`; optional.

Success `200`: `application/json`: `Run`.

Known domain failures: `404` NOT_FOUND; `409` REVISION_CONFLICT.

#### `POST /api/papers/{ident}/export`

Resolve an existing paper/project ID. Optional expected_revision compares PaperDocument revision. For a source ZIP, optional source and bibtex fields export the editor's current draft without saving it. format=pdf requires an existing compiled PDF of the CURRENT revision; source ZIP includes a PDF only when its source matches the saved revision. Source ZIP excludes .aux/.blg/.log artifacts.

| Parameter | Location | Type | Required | Default / description |
| --- | --- | --- | --- | --- |
| `ident` | path | string | yes |  |

Body: `application/json`: `PaperExportRequest`; optional.

Success `200`: `application/zip`: `string`; `application/pdf`: `string`.

Known domain failures: `403` PATH_ESCAPE; `404` NOT_FOUND or MISSING_ARTIFACT; `409` REVISION_CONFLICT, PDF_UNAVAILABLE or STALE_PDF.

#### `POST /api/papers/{ident}/figures`

Require CURRENT paper expected_revision, figure_id and anchor_text. Figure must be from this project and ready_for_review/available with a rendered PDF or image. Copy chosen output/supporting assets, insert caption/local label and binding, increment paper revision and invalidate layout checks. caption defaults figure caption/title; span defaults column. Return PaperRecord.

| Parameter | Location | Type | Required | Default / description |
| --- | --- | --- | --- | --- |
| `ident` | path | string | yes |  |

Body: `application/json`: `PaperFigureRequest`; required.

Success `200`: `application/json`: `PaperRecord`.

Known domain failures: `403` PATH_ESCAPE; `404` NOT_FOUND or MISSING_ARTIFACT; `409` REVISION_CONFLICT or FIGURE_UNAVAILABLE; `422` CROSS_PROJECT or INVALID_FIGURE_ANCHOR.

#### `POST /api/papers/{ident}/generate`

Resolve paper/project ID. Require a nonempty run_ids list of completed experiment/command/agent runs in this project, each with a readable metrics file containing finite numeric measurements. Reject invalid evidence before enqueue and recheck it during generation. manuscript_type defaults full_paper. Other generation options are extensible. Return Run for generation/review workflow, not automatically applied manuscript text.

| Parameter | Location | Type | Required | Default / description |
| --- | --- | --- | --- | --- |
| `ident` | path | string | yes |  |

Body: `application/json`: `PaperGenerationRequest`; required.

Success `200`: `application/json`: `Run`.

Known domain failures: `404` NOT_FOUND; `409` GOAL_APPLICABILITY_REQUIRED; `422` EVIDENCE_REQUIRED or INVALID_EVIDENCE.

#### `POST /api/papers/{ident}/layout`

Require current PaperDocument expected_revision. Merge body.layout with saved layout and normalize the chosen template; template defaults saved template, inferred iclr2027 source, or article. Return Run from the layout workflow.

| Parameter | Location | Type | Required | Default / description |
| --- | --- | --- | --- | --- |
| `ident` | path | string | yes |  |

Body: `application/json`: `PaperLayoutRequest`; required.

Success `200`: `application/json`: `Run`.

Known domain failures: `404` NOT_FOUND; `409` REVISION_CONFLICT; `422` INVALID_LAYOUT.

#### `POST /api/papers/{ident}/revise`

Resolve paper/project ID, optionally compare PaperDocument revision, capture saved source/revision and pass the extensible body to paper_revise. Return Run. Saved manuscript edits are applied via review acceptance or explicit manual editing.

| Parameter | Location | Type | Required | Default / description |
| --- | --- | --- | --- | --- |
| `ident` | path | string | yes |  |

Body: `application/json`: `PaperRevisionRequest`; required.

Success `200`: `application/json`: `Run`.

Known domain failures: `404` NOT_FOUND; `409` REVISION_CONFLICT.

#### `POST /api/projects/{ident}/writing/review`

body.source is text (default empty). Return character offsets and one-based line numbers. replacement:null means an evidence judgment is required; never apply it as empty replacement. This endpoint does not edit a saved paper.

| Parameter | Location | Type | Required | Default / description |
| --- | --- | --- | --- | --- |
| `ident` | path | string | yes |  |

Body: `application/json`: `WritingReviewRequest`; required.

Success `200`: `application/json`: `WritingReview`.

Known domain failures: `404` NOT_FOUND.

#### `POST /api/reviews/{ident}/apply`

Require current PAPER expected_revision and valid indices. The saved review.paper_revision must still match. Generation reviews accept indices:[0] for their complete saved bundle; ordinary edits require each original passage to occur exactly once. Return updated PaperRecord, increment revision and update working source.

| Parameter | Location | Type | Required | Default / description |
| --- | --- | --- | --- | --- |
| `ident` | path | string | yes |  |

Body: `application/json`: `ReviewApplyRequest`; required.

Success `200`: `application/json`: `PaperRecord`.

Known domain failures: `404` NOT_FOUND; `409` REVISION_CONFLICT or AMBIGUOUS_SPAN; `422` INVALID_SELECTION.

#### `GET /api/writing-policy`

Return extensible writing profile plus manuscript_contract and revision_contract strings.

Success `200`: `application/json`: `object`.

### Progress

#### `GET /api/projects/{ident}/progress`

Owner-scoped reporting domain. Reads do not invoke a model, verifier or remote host. Source offsets are UTF-8 bytes (end-exclusive); lines are one-based. Inventory and retained current text are bounded, with explicit coverage. Refresh may consume the authorized provider allowance; duplicate requests coalesce. Narration selects service-rendered facts and cannot assign status or scientific acceptance.

| Parameter | Location | Type | Required | Default / description |
| --- | --- | --- | --- | --- |
| `ident` | path | string | yes |  |

Success `200`: `application/json`: `ProjectProgressSnapshot`.

Known domain failures: `404` Project/source unavailable; `409` Revision conflict, rebuilding or narration disabled.

#### `GET /api/projects/{ident}/progress/artifacts/{file_id}`

Owner-only bounded artifact. Epoch, generation, scope ownership and current bytes must match. Changed/deleted sources return an error. Does not read unbounded binary content.

| Parameter | Location | Type | Required | Default / description |
| --- | --- | --- | --- | --- |
| `ident` | path | string | yes |  |
| `file_id` | path | string | yes |  |
| `epoch` | query | string | yes |  |
| `generation` | query | integer | yes |  minimum=1. |

Success `200`: `application/json`: `string`.

Known domain failures: `404` Source unavailable; `409` Source changed or not retained.

#### `GET /api/projects/{ident}/progress/scopes`

Owner-scoped reporting domain. Reads do not invoke a model, verifier or remote host. Source offsets are UTF-8 bytes (end-exclusive); lines are one-based. Inventory and retained current text are bounded, with explicit coverage. Refresh may consume the authorized provider allowance; duplicate requests coalesce. Narration selects service-rendered facts and cannot assign status or scientific acceptance.

| Parameter | Location | Type | Required | Default / description |
| --- | --- | --- | --- | --- |
| `ident` | path | string | yes |  |
| `cursor` | query | string | no | Default ``.  |
| `limit` | query | integer | no | Default `50`.  minimum=1, maximum=200. |
| `kind` | query | string or null | no |  |
| `object_id` | query | string or null | no |  |

Success `200`: `application/json`: `ScopePage`.

Known domain failures: `404` Project/source unavailable; `409` Revision conflict, rebuilding or narration disabled.

#### `POST /api/projects/{ident}/progress/source`

Owner-scoped reporting domain. Reads do not invoke a model, verifier or remote host. Source offsets are UTF-8 bytes (end-exclusive); lines are one-based. Inventory and retained current text are bounded, with explicit coverage. Refresh may consume the authorized provider allowance; duplicate requests coalesce. Narration selects service-rendered facts and cannot assign status or scientific acceptance.

| Parameter | Location | Type | Required | Default / description |
| --- | --- | --- | --- | --- |
| `ident` | path | string | yes |  |

Body: `application/json`: `SourceRef`; required.

Success `200`: `application/json`: `SourceView`.

Known domain failures: `404` Project/source unavailable; `409` Revision conflict, rebuilding or narration disabled.

#### `GET /api/projects/{ident}/progress/sources`

Owner-scoped reporting domain. Reads do not invoke a model, verifier or remote host. Source offsets are UTF-8 bytes (end-exclusive); lines are one-based. Inventory and retained current text are bounded, with explicit coverage. Refresh may consume the authorized provider allowance; duplicate requests coalesce. Narration selects service-rendered facts and cannot assign status or scientific acceptance.

| Parameter | Location | Type | Required | Default / description |
| --- | --- | --- | --- | --- |
| `ident` | path | string | yes |  |
| `scope_id` | query | string | yes |  |
| `cursor` | query | string | no | Default ``.  |
| `limit` | query | integer | no | Default `50`.  minimum=1, maximum=200. |

Success `200`: `application/json`: `FilePage`.

Known domain failures: `404` Project/source unavailable; `409` Revision conflict, rebuilding or narration disabled.

#### `GET /api/projects/{ident}/reporter-settings`

Owner-scoped reporting domain. Reads do not invoke a model, verifier or remote host. Source offsets are UTF-8 bytes (end-exclusive); lines are one-based. Inventory and retained current text are bounded, with explicit coverage. Refresh may consume the authorized provider allowance; duplicate requests coalesce. Narration selects service-rendered facts and cannot assign status or scientific acceptance.

| Parameter | Location | Type | Required | Default / description |
| --- | --- | --- | --- | --- |
| `ident` | path | string | yes |  |

Success `200`: `application/json`: `ReporterSettingsView`.

Known domain failures: `404` Project/source unavailable; `409` Revision conflict, rebuilding or narration disabled.

#### `PATCH /api/projects/{ident}/reporter-settings`

Owner-scoped reporting domain. Reads do not invoke a model, verifier or remote host. Source offsets are UTF-8 bytes (end-exclusive); lines are one-based. Inventory and retained current text are bounded, with explicit coverage. Refresh may consume the authorized provider allowance; duplicate requests coalesce. Narration selects service-rendered facts and cannot assign status or scientific acceptance.

| Parameter | Location | Type | Required | Default / description |
| --- | --- | --- | --- | --- |
| `ident` | path | string | yes |  |

Body: `application/json`: `ReporterSettingsWrite`; required.

Success `200`: `application/json`: `ReporterSettingsView`.

Known domain failures: `404` Project/source unavailable; `409` Revision conflict, rebuilding or narration disabled.

#### `GET /api/projects/{ident}/reports`

Owner-scoped reporting domain. Reads do not invoke a model, verifier or remote host. Source offsets are UTF-8 bytes (end-exclusive); lines are one-based. Inventory and retained current text are bounded, with explicit coverage. Refresh may consume the authorized provider allowance; duplicate requests coalesce. Narration selects service-rendered facts and cannot assign status or scientific acceptance.

| Parameter | Location | Type | Required | Default / description |
| --- | --- | --- | --- | --- |
| `ident` | path | string | yes |  |
| `cursor` | query | string | no | Default ``.  |
| `limit` | query | integer | no | Default `20`.  minimum=1, maximum=50. |

Success `200`: `application/json`: `ReportPage`.

Known domain failures: `404` Project/source unavailable; `409` Revision conflict, rebuilding or narration disabled.

#### `GET /api/projects/{ident}/reports/latest`

Owner-scoped reporting domain. Reads do not invoke a model, verifier or remote host. Source offsets are UTF-8 bytes (end-exclusive); lines are one-based. Inventory and retained current text are bounded, with explicit coverage. Refresh may consume the authorized provider allowance; duplicate requests coalesce. Narration selects service-rendered facts and cannot assign status or scientific acceptance.

| Parameter | Location | Type | Required | Default / description |
| --- | --- | --- | --- | --- |
| `ident` | path | string | yes |  |

Success `200`: `application/json`: `ReporterJob or null`.

Known domain failures: `404` Project/source unavailable; `409` Revision conflict, rebuilding or narration disabled.

#### `POST /api/projects/{ident}/reports/refresh`

Owner-scoped reporting domain. Reads do not invoke a model, verifier or remote host. Source offsets are UTF-8 bytes (end-exclusive); lines are one-based. Inventory and retained current text are bounded, with explicit coverage. Refresh may consume the authorized provider allowance; duplicate requests coalesce. Narration selects service-rendered facts and cannot assign status or scientific acceptance.

| Parameter | Location | Type | Required | Default / description |
| --- | --- | --- | --- | --- |
| `ident` | path | string | yes |  |

Body: `application/json`: `ReporterRefresh`; required.

Success `200`: `application/json`: `ReporterJob`.

Known domain failures: `404` Project/source unavailable; `409` Revision conflict, rebuilding or narration disabled.

### Projects

#### `GET /api/projects`

Return a bare array ordered by updated_at descending. Optional archived filters the rows; limit is capped at 200 and offset is clamped to zero. Pagination is not wrapped in a total/cursor object.

| Parameter | Location | Type | Required | Default / description |
| --- | --- | --- | --- | --- |
| `archived` | query | boolean or null | no |  |
| `limit` | query | integer | no | Default `100`.  |
| `offset` | query | integer | no | Default `0`.  |

Success `200`: `application/json`: `Project[]`.

#### `POST /api/projects`

Create a project and its main workspace/graph. When mode is omitted, use the saved default_mode preference, falling back to assisted if unset or invalid; an explicit mode takes precedence. Return the persisted project; successful creation uses HTTP 200.

Body: `application/json`: `ProjectCreate`; required.

Success `200`: `application/json`: `Project`.

#### `DELETE /api/projects/{ident}`

Cancel active local/container/remote execution, interrupt cost reservations, delete project database records and remove its workspace. Return the removed identifier.

| Parameter | Location | Type | Required | Default / description |
| --- | --- | --- | --- | --- |
| `ident` | path | string | yes |  |

Success `200`: `application/json`: `Deleted`.

Known domain failures: `404` NOT_FOUND.

#### `GET /api/projects/{ident}`

Return the project with graph_meta omitted. Read /graph for the editable graph state.

| Parameter | Location | Type | Required | Default / description |
| --- | --- | --- | --- | --- |
| `ident` | path | string | yes |  |

Success `200`: `application/json`: `Project`.

Known domain failures: `404` NOT_FOUND.

#### `PATCH /api/projects/{ident}`

Apply only name, description, goal, current_direction, archived, mode, budget and config. budget/config replace their whole values. Unknown top-level keys are ignored. Optional expected_revision compares the PROJECT graph revision; every successful patch increments it. Goal edits mark node contexts for refresh and emit project_changed.

| Parameter | Location | Type | Required | Default / description |
| --- | --- | --- | --- | --- |
| `ident` | path | string | yes |  |

Body: `application/json`: `ProjectPatch`; required.

Success `200`: `application/json`: `Project`.

Known domain failures: `404` NOT_FOUND; `409` REVISION_CONFLICT.

#### `POST /api/projects/{ident}/duplicate`

Copy graph/resources/manuscripts/runs/files with remapped identifiers, excluding symlinks and private graph edit history. Active copied runs are marked interrupted; duplication does not replay experiments. Return the new project.

| Parameter | Location | Type | Required | Default / description |
| --- | --- | --- | --- | --- |
| `ident` | path | string | yes |  |

Success `200`: `application/json`: `Project`.

Known domain failures: `404` NOT_FOUND.

### Repositories

#### `POST /api/projects/{ident}/repositories/clone`

Only repository/request_id top-level fields are accepted. Normalize the input; all Git/network work runs in a worker. A reused request_id must identify the identical normalized repository submission or return 409. Return queued Run.

| Parameter | Location | Type | Required | Default / description |
| --- | --- | --- | --- | --- |
| `ident` | path | string | yes |  |

Body: `application/json`: `RepositoryCloneRequest`; required.

Success `200`: `application/json`: `Run`.

Known domain failures: `404` NOT_FOUND; `409` REQUEST_ID_CONFLICT; `422` INVALID_REPOSITORY, INVALID_REQUEST_ID or repository field errors.

#### `GET /api/runs/{ident}/repository`

Return repository:null before a source manifest exists. A present manifest must be a bounded valid object with a normalized source URL/ref/directory/transport and a Git commit identity. Expose only those provenance fields, never a credential profile/key.

| Parameter | Location | Type | Required | Default / description |
| --- | --- | --- | --- | --- |
| `ident` | path | string | yes |  |

Success `200`: `application/json`: `RunRepository`.

Known domain failures: `404` NOT_FOUND; `422` INVALID_REPOSITORY_MANIFEST.

### Research

#### `POST /api/analysis/run`

Enqueue an analysis task over declared real inputs; output fields depend on the selected analysis type. Return Run, not the final scientific output.

Body: `application/json`: `QueuedTaskRequest`; required.

Success `200`: `application/json`: `Run`.

Known domain failures: `400` Scheduling/input errors; `404` NOT_FOUND.

#### `POST /api/ideas/{ident}/adopt`

Create hypothesis plus configured experiment, or hypothesis/implementation/experiment nodes when implementation is needed. Optional expected_revision is the project graph revision. request_id is 1..120 characters and adoption-specific; replaying the same request_id for the same idea returns the current graph. An idea that already has nodes in the graph rejects a second adoption without that receipt with IDEA_ALREADY_ADOPTED (409). Undoing the adoption permits a new path. inputs accepts up to 50 explicit path/reference inputs in this project. config is the executable experiment configuration, not a dummy case. Return the updated GraphState as one graph edit/undo.

| Parameter | Location | Type | Required | Default / description |
| --- | --- | --- | --- | --- |
| `ident` | path | string | yes |  |

Body: `application/json`: `IdeaAdoptRequest`; optional.

Success `200`: `application/json`: `GraphState`.

Known domain failures: `404` NOT_FOUND; `409` REVISION_CONFLICT, REQUEST_ID_CONFLICT, BRANCH_INACTIVE or IDEA_ALREADY_ADOPTED; `422` INVALID_REQUEST_ID, INVALID_EXPERIMENT_CONFIG, INVALID_INPUTS, CROSS_PROJECT, MISSING_BRANCH, INVALID_TITLE or INVALID_INSTRUCTIONS.

#### `PATCH /api/projects/{ident}/objective`

Trim metric; blank/omitted metric clears the objective. direction defaults min and must be min/max. comparison_fields defaults dataset/protocol_version. Increment PROJECT revision and return the objective object only. No expected_revision guard.

| Parameter | Location | Type | Required | Default / description |
| --- | --- | --- | --- | --- |
| `ident` | path | string | yes |  |

Body: `application/json`: `ObjectiveRequest`; required.

Success `200`: `application/json`: `JsonObject`.

Known domain failures: `404` NOT_FOUND; `422` INVALID_OBJECTIVE.

#### `GET /api/projects/{ident}/publication`

Assess current saved manifest/design/evidence/compiled manuscript and source-bound verification. Return profile-dependent coverage and gaps; ready does not mean a conference has accepted the paper.

| Parameter | Location | Type | Required | Default / description |
| --- | --- | --- | --- | --- |
| `ident` | path | string | yes |  |

Success `200`: `application/json`: `PublicationAudit`.

Known domain failures: `404` NOT_FOUND.

#### `PATCH /api/projects/{ident}/publication`

Merge body into the saved profile, normalize full_submission/operational requirements, increment PROJECT revision and emit project_changed. No expected_revision guard. Return normalized profile/revision.

| Parameter | Location | Type | Required | Default / description |
| --- | --- | --- | --- | --- |
| `ident` | path | string | yes |  |

Body: `application/json`: `PublicationProfilePatch`; required.

Success `200`: `application/json`: `object`.

Known domain failures: `404` NOT_FOUND; `422` INVALID_PUBLICATION_PROFILE.

#### `GET /api/projects/{ident}/research`

Default returns objective, declared comparable trial groups, current controller configuration, active runs, controller-origin decisions and run counts. Optional/required verification policies control comparison eligibility. Trial fields are absent until applicable; null value is unmeasured, not zero. overview=true returns bounded recorded lifecycle and counts with empty trials and explicit coverage, without filesystem/verification replay; comparisons require the default explicit inspection.

| Parameter | Location | Type | Required | Default / description |
| --- | --- | --- | --- | --- |
| `ident` | path | string | yes |  |
| `overview` | query | boolean | no | Default `False`.  |

Success `200`: `application/json`: `ResearchState`.

Known domain failures: `404` NOT_FOUND.

#### `GET /api/projects/{ident}/research/analysis`

Return every analysis receipt in newest-first order, retaining full IDs, requested focus/question, saved project revision, actual status/error and report-file availability. Report availability alone does not establish completed execution or report integrity; read the result endpoint.

| Parameter | Location | Type | Required | Default / description |
| --- | --- | --- | --- | --- |
| `ident` | path | string | yes |  |

Success `200`: `application/json`: `ResearchAnalysisHistory`.

Known domain failures: `404` NOT_FOUND.

#### `POST /api/projects/{ident}/research/analysis`

Require a saved request_id and reviewed expected_revision; optional expected_fingerprint also rejects changed results/materials. Save the complete sanitized context snapshot, enqueue research_analysis through the existing provider/time/spending controls, and return Run with provider_snapshot/env omitted. The run has no node/branch execution binding and does not modify the graph/controller. Same request and payload replay the saved receipt before the revision guard; changed intent under the same ID conflicts. Literature retrieval uses selected Crossref/arXiv sources during execution. Only actual retrieved/library citation IDs are accepted. Advice remains a proposed Analysis record and cannot verify science or execute its suggestions.

| Parameter | Location | Type | Required | Default / description |
| --- | --- | --- | --- | --- |
| `ident` | path | string | yes |  |

Body: `application/json`: `AnalysisRequest`; required.

Success `200`: `application/json`: `Run`.

Known domain failures: `404` NOT_FOUND, NODE_NOT_FOUND or BRANCH_NOT_FOUND; `409` REQUEST_ID_CONFLICT, REVISION_CONFLICT, CONTEXT_CHANGED or TIME_BUDGET_EXHAUSTED; `422` PROVIDER_REQUIRED, invalid scope or request validation.

#### `GET /api/projects/{ident}/research/analysis/context`

Return every saved research node/path/dependency, scientific run and linked resource, verification receipt, tool receipt and material locator. scope changes the focus while retaining the full project context. Material text is not loaded in this preview. The snapshot fingerprint includes execution/source changes beyond the graph revision. AI analysis history is separate from scientific source records. Provider availability is configuration readback, not a connection test.

| Parameter | Location | Type | Required | Default / description |
| --- | --- | --- | --- | --- |
| `ident` | path | string | yes |  |
| `scope` | query | string | no | Default `project`.  Values: project, node, branch. |
| `node_id` | query | string or null | no |  |
| `branch_id` | query | string or null | no |  |

Success `200`: `application/json`: `ResearchAnalysisContext`.

Known domain failures: `404` NOT_FOUND, NODE_NOT_FOUND or BRANCH_NOT_FOUND; `422` NODE_REQUIRED, BRANCH_REQUIRED or SCOPE_CONFLICT.

#### `GET /api/projects/{ident}/research/route-health`

Calculate actual repeated current-revision failures, unchanged repeated counterexample checks and planning without execution. Return editable diagnostic thresholds, signals and reviewed run IDs. New counterexample evidence is progress. This deterministic view does not judge semantic goal drift; enqueue the full route review for that.

| Parameter | Location | Type | Required | Default / description |
| --- | --- | --- | --- | --- |
| `ident` | path | string | yes |  |

Success `200`: `application/json`: `RouteHealth`.

Known domain failures: `404` NOT_FOUND.

#### `POST /api/projects/{ident}/research/route-review`

Enqueue research_route_review with extensible body/request_id and record its trigger/covered run IDs in controller.route_review. Return Run; the review output is saved after execution and does not directly verify experimental results.

| Parameter | Location | Type | Required | Default / description |
| --- | --- | --- | --- | --- |
| `ident` | path | string | yes |  |

Body: `application/json`: `JsonObject`; optional.

Success `200`: `application/json`: `Run`.

Known domain failures: `404` NOT_FOUND.

#### `POST /api/projects/{ident}/research/{action}`

Actions start/pause/stop. start sets PLAN/running with optional branch_id/required_artifacts/autonomous and resumes previously paused runs. Legacy max_cycles is history only; goals, actual resource hard limits, cancellation and actionable blockers govern lifetime. A selected branch must belong to the project and have active status; restore pruned, archived, or disabled branches before starting. pause/stop process-control affected active runs after updating controller configuration. Return controller fields plus process_control_errors; HTTP 200 can contain individual process-control errors and must be inspected.

| Parameter | Location | Type | Required | Default / description |
| --- | --- | --- | --- | --- |
| `ident` | path | string | yes |  |
| `action` | path | string | yes |  Values: start, pause, stop. |

Body: `application/json`: `ResearchControlRequest`; optional.

Success `200`: `application/json`: `ResearchControl`.

Known domain failures: `404` NOT_FOUND, UNKNOWN_ACTION, or UNKNOWN_BRANCH; `409` BRANCH_NOT_RUNNABLE.

#### `GET /api/projects/{ident}/usage`

Return project configured-rate costs/reservations/time and active Agent limits. Provider rows explicitly describe all_projects scope; do not subtract them from the project allowance a second time. Missing spending limits return null remaining_usd.

| Parameter | Location | Type | Required | Default / description |
| --- | --- | --- | --- | --- |
| `ident` | path | string | yes |  |

Success `200`: `application/json`: `ProjectUsage`.

Known domain failures: `404` NOT_FOUND.

#### `GET /api/publication-profile`

Return default PublicationProfile, textual instructions and available role names. Profile targets are editable design requirements, not completed measurements.

Success `200`: `application/json`: `object`.

#### `POST /api/research/ideas`

Enqueue idea generation; scientific judgments and task-specific options are in the extensible request body. Return Run, not the final scientific output.

Body: `application/json`: `QueuedTaskRequest`; required.

Success `200`: `application/json`: `Run`.

Known domain failures: `400` Scheduling/input errors; `404` NOT_FOUND.

#### `POST /api/research/proposals/{ident}/apply`

ident is a Hypothesis/proposal record. Optional expected_revision compares PROJECT graph revision. commands defaults saved proposal commands; indices defaults all. Apply sequential kernel commands and return graph/accepted_indices. Empty selection is rejected. Each selected command can have filesystem effects; client should preview and supply valid indices.

| Parameter | Location | Type | Required | Default / description |
| --- | --- | --- | --- | --- |
| `ident` | path | string | yes |  |

Body: `application/json`: `ProposalApplyRequest`; required.

Success `200`: `application/json`: `ProposalApplied`.

Known domain failures: `400` EMPTY_SELECTION or graph command errors; `404` NOT_FOUND; `409` REVISION_CONFLICT or graph conflicts.

#### `POST /api/research/suggest-paths`

Enqueue path proposals. scope=node sends the selected node and its execution ancestors; scope=branch sends only the selected node's branch; scope=project sends the full graph. Node and branch scopes require node_id. Return Run, not the final scientific output.

Body: `application/json`: `QueuedTaskRequest`; required.

Success `200`: `application/json`: `Run`.

Known domain failures: `400` Scheduling/input errors; `404` NOT_FOUND.

#### `POST /api/reviews`

Enqueue scientific review; POST /reviews does not directly create a Review resource record. Return Run, not the final scientific output.

Body: `application/json`: `QueuedTaskRequest`; required.

Success `200`: `application/json`: `Run`.

Known domain failures: `400` Scheduling/input errors; `404` NOT_FOUND.

#### `GET /api/runs/{ident}/research-analysis`

For a research_analysis run, return actual lifecycle/error, original snapshot identities, current/stale reasons, source citations/search receipts and artifact locators. A report is exposed only after completed execution and matching recorded report SHA256; missing/changed files yield report:null and artifact_error. Graph edits, later scientific run changes and source-file changes make the advice stale while retaining its history. Scientific verification and AI advice remain distinct.

| Parameter | Location | Type | Required | Default / description |
| --- | --- | --- | --- | --- |
| `ident` | path | string | yes |  |

Success `200`: `application/json`: `ResearchAnalysisResult`.

Known domain failures: `404` NOT_FOUND; `422` INVALID_RUN_KIND.

#### `POST /api/theory/check`

Require project_id/expression; variable defaults x. kind differentiate/solve/numeric selects an operation; any other/omitted kind simplifies. values supplies numeric substitutions. Restricted arithmetic grammar permits sin/cos/exp/log/sqrt/abs, bounded constants/powers and no arbitrary eval. Save a checked Derivation RecordItem, with result/latex and an explicit supplied-expression scope.

Body: `application/json`: `SymbolicCheckRequest`; required.

Success `200`: `application/json`: `RecordItem`.

Known domain failures: `400` INVALID_EXPRESSION; `404` NOT_FOUND; `422` EXPRESSION_FAILED.

### Research evolution

#### `GET /api/projects/{project_id}/evaluation-protocols`

Return a bare array of original evaluation versions ordered by creation time. Definitions and feedback history remain inspectable after revision.

| Parameter | Location | Type | Required | Default / description |
| --- | --- | --- | --- | --- |
| `project_id` | path | string | yes |  |

Success `200`: `application/json`: `EvaluationProtocolVersion[]`.

Known domain failures: `404` NOT_FOUND.

#### `POST /api/projects/{project_id}/evaluation-protocols`

Record a nonempty definition with optional protocol_id, expected revisions and request_id. A missing protocol_id creates a new explicit identity. Actual feedback use marks evaluation adaptive and persists across revisions of that scope. Declaring independent_confirmation is a scope label, not proof of execution or independent verification. Accepted replay returns the original version; changed intent conflicts.

| Parameter | Location | Type | Required | Default / description |
| --- | --- | --- | --- | --- |
| `project_id` | path | string | yes |  |

Body: `application/json`: `EvaluationProtocolRevisionRequest`; required.

Success `200`: `application/json`: `EvaluationProtocolVersion`.

Known domain failures: `404` NOT_FOUND; `409` REVISION_CONFLICT or REQUEST_ID_CONFLICT; `422` Invalid protocol, classification, evidence or request identity.

#### `GET /api/projects/{project_id}/goal-contract`

Return the current editable Goal Contract and every original version. Legacy projects initialize their original contract under the project writer lock. Historical results retain their saved goal conditions.

| Parameter | Location | Type | Required | Default / description |
| --- | --- | --- | --- | --- |
| `project_id` | path | string | yes |  |

Success `200`: `application/json`: `GoalContractHistory`.

Known domain failures: `404` NOT_FOUND.

#### `POST /api/projects/{project_id}/goal-contract/versions`

Revise objective, criteria or contract with a reason and existing same-project evidence/branch references. Revision guards are optional. request_id replays the original accepted result before stale revision checks; conflicting reuse is rejected. A completed controller can reopen; prior runs and resource accounting are preserved.

| Parameter | Location | Type | Required | Default / description |
| --- | --- | --- | --- | --- |
| `project_id` | path | string | yes |  |

Body: `application/json`: `GoalContractRevisionRequest`; required.

Success `200`: `application/json`: `GoalContractHistory`.

Known domain failures: `404` NOT_FOUND; `409` REVISION_CONFLICT or REQUEST_ID_CONFLICT; `422` Invalid goal, evidence, branch or request identity.

### Resources

#### `GET /api/analyses`

Require query project_id. Return a bare resource array ordered by updated_at descending; limit capped at 500, offset clamped at zero. Kind-specific data is extensible.

| Parameter | Location | Type | Required | Default / description |
| --- | --- | --- | --- | --- |
| `project_id` | query | string | yes |  |
| `limit` | query | integer | no | Default `100`.  |
| `offset` | query | integer | no | Default `0`.  |

Success `200`: `application/json`: `RecordItem[]`.

Known domain failures: `404` NOT_FOUND.

#### `POST /api/analyses`

ResourceCreate supplies project_id/title/data. Create the record and emit artifact_available. This creates editable metadata; it does not execute the related scientific task.

Body: `application/json`: `ResourceCreate`; required.

Success `200`: `application/json`: `RecordItem`.

Known domain failures: `404` NOT_FOUND.

#### `DELETE /api/analyses/{ident}`

Delete the metadata record and mark consuming materials stale; this handler does not delete all associated workspace files. Return deleted identifier.

| Parameter | Location | Type | Required | Default / description |
| --- | --- | --- | --- | --- |
| `ident` | path | string | yes |  |

Success `200`: `application/json`: `Deleted`.

Known domain failures: `404` NOT_FOUND.

#### `GET /api/analyses/{ident}`

Return the persisted resource; data structure depends on the resource kind.

| Parameter | Location | Type | Required | Default / description |
| --- | --- | --- | --- | --- |
| `ident` | path | string | yes |  |

Success `200`: `application/json`: `RecordItem`.

Known domain failures: `404` NOT_FOUND.

#### `PATCH /api/analyses/{ident}`

Optional expected_revision compares this RESOURCE revision, default current. Replace supplied title/status; shallow-merge data unless replace_data:true, which replaces the complete data object. Ignore other top-level keys. Increment resource revision and mark consuming materials stale.

| Parameter | Location | Type | Required | Default / description |
| --- | --- | --- | --- | --- |
| `ident` | path | string | yes |  |

Body: `application/json`: `ResourcePatch`; required.

Success `200`: `application/json`: `RecordItem`.

Known domain failures: `404` NOT_FOUND; `409` REVISION_CONFLICT.

#### `GET /api/claims`

Require query project_id. Return a bare resource array ordered by updated_at descending; limit capped at 500, offset clamped at zero. Kind-specific data is extensible.

| Parameter | Location | Type | Required | Default / description |
| --- | --- | --- | --- | --- |
| `project_id` | query | string | yes |  |
| `limit` | query | integer | no | Default `100`.  |
| `offset` | query | integer | no | Default `0`.  |

Success `200`: `application/json`: `RecordItem[]`.

Known domain failures: `404` NOT_FOUND.

#### `POST /api/claims`

ResourceCreate supplies project_id/title/data. Create the record and emit artifact_available. This creates editable metadata; it does not execute the related scientific task.

Body: `application/json`: `ResourceCreate`; required.

Success `200`: `application/json`: `RecordItem`.

Known domain failures: `404` NOT_FOUND.

#### `DELETE /api/claims/{ident}`

Delete the metadata record and mark consuming materials stale; this handler does not delete all associated workspace files. Return deleted identifier.

| Parameter | Location | Type | Required | Default / description |
| --- | --- | --- | --- | --- |
| `ident` | path | string | yes |  |

Success `200`: `application/json`: `Deleted`.

Known domain failures: `404` NOT_FOUND.

#### `GET /api/claims/{ident}`

Return the persisted resource; data structure depends on the resource kind.

| Parameter | Location | Type | Required | Default / description |
| --- | --- | --- | --- | --- |
| `ident` | path | string | yes |  |

Success `200`: `application/json`: `RecordItem`.

Known domain failures: `404` NOT_FOUND.

#### `PATCH /api/claims/{ident}`

Optional expected_revision compares this RESOURCE revision, default current. Replace supplied title/status; shallow-merge data unless replace_data:true, which replaces the complete data object. Ignore other top-level keys. Increment resource revision and mark consuming materials stale.

| Parameter | Location | Type | Required | Default / description |
| --- | --- | --- | --- | --- |
| `ident` | path | string | yes |  |

Body: `application/json`: `ResourcePatch`; required.

Success `200`: `application/json`: `RecordItem`.

Known domain failures: `404` NOT_FOUND; `409` REVISION_CONFLICT.

#### `GET /api/datasets`

Require query project_id. Return a bare resource array ordered by updated_at descending; limit capped at 500, offset clamped at zero. Kind-specific data is extensible.

| Parameter | Location | Type | Required | Default / description |
| --- | --- | --- | --- | --- |
| `project_id` | query | string | yes |  |
| `limit` | query | integer | no | Default `100`.  |
| `offset` | query | integer | no | Default `0`.  |

Success `200`: `application/json`: `RecordItem[]`.

Known domain failures: `404` NOT_FOUND.

#### `POST /api/datasets`

ResourceCreate supplies project_id/title/data. Create the record and emit artifact_available. This creates editable metadata; it does not execute the related scientific task.

Body: `application/json`: `ResourceCreate`; required.

Success `200`: `application/json`: `RecordItem`.

Known domain failures: `404` NOT_FOUND.

#### `DELETE /api/datasets/{ident}`

Delete the metadata record and mark consuming materials stale; this handler does not delete all associated workspace files. Return deleted identifier.

| Parameter | Location | Type | Required | Default / description |
| --- | --- | --- | --- | --- |
| `ident` | path | string | yes |  |

Success `200`: `application/json`: `Deleted`.

Known domain failures: `404` NOT_FOUND.

#### `GET /api/datasets/{ident}`

Return the persisted resource; data structure depends on the resource kind.

| Parameter | Location | Type | Required | Default / description |
| --- | --- | --- | --- | --- |
| `ident` | path | string | yes |  |

Success `200`: `application/json`: `RecordItem`.

Known domain failures: `404` NOT_FOUND.

#### `PATCH /api/datasets/{ident}`

Optional expected_revision compares this RESOURCE revision, default current. Replace supplied title/status; shallow-merge data unless replace_data:true, which replaces the complete data object. Ignore other top-level keys. Increment resource revision and mark consuming materials stale.

| Parameter | Location | Type | Required | Default / description |
| --- | --- | --- | --- | --- |
| `ident` | path | string | yes |  |

Body: `application/json`: `ResourcePatch`; required.

Success `200`: `application/json`: `RecordItem`.

Known domain failures: `404` NOT_FOUND; `409` REVISION_CONFLICT.

#### `GET /api/experiments`

Require query project_id. Return a bare resource array ordered by updated_at descending; limit capped at 500, offset clamped at zero. Kind-specific data is extensible.

| Parameter | Location | Type | Required | Default / description |
| --- | --- | --- | --- | --- |
| `project_id` | query | string | yes |  |
| `limit` | query | integer | no | Default `100`.  |
| `offset` | query | integer | no | Default `0`.  |

Success `200`: `application/json`: `RecordItem[]`.

Known domain failures: `404` NOT_FOUND.

#### `POST /api/experiments`

ResourceCreate supplies project_id/title/data. Create the record and emit artifact_available. This creates editable metadata; it does not execute the related scientific task.

Body: `application/json`: `ResourceCreate`; required.

Success `200`: `application/json`: `RecordItem`.

Known domain failures: `404` NOT_FOUND.

#### `DELETE /api/experiments/{ident}`

Delete the metadata record and mark consuming materials stale; this handler does not delete all associated workspace files. Return deleted identifier.

| Parameter | Location | Type | Required | Default / description |
| --- | --- | --- | --- | --- |
| `ident` | path | string | yes |  |

Success `200`: `application/json`: `Deleted`.

Known domain failures: `404` NOT_FOUND.

#### `GET /api/experiments/{ident}`

Return the persisted resource; data structure depends on the resource kind.

| Parameter | Location | Type | Required | Default / description |
| --- | --- | --- | --- | --- |
| `ident` | path | string | yes |  |

Success `200`: `application/json`: `RecordItem`.

Known domain failures: `404` NOT_FOUND.

#### `PATCH /api/experiments/{ident}`

Optional expected_revision compares this RESOURCE revision, default current. Replace supplied title/status; shallow-merge data unless replace_data:true, which replaces the complete data object. Ignore other top-level keys. Increment resource revision and mark consuming materials stale.

| Parameter | Location | Type | Required | Default / description |
| --- | --- | --- | --- | --- |
| `ident` | path | string | yes |  |

Body: `application/json`: `ResourcePatch`; required.

Success `200`: `application/json`: `RecordItem`.

Known domain failures: `404` NOT_FOUND; `409` REVISION_CONFLICT.

#### `GET /api/figures`

Require query project_id. Return a bare resource array ordered by updated_at descending; limit capped at 500, offset clamped at zero. Kind-specific data is extensible.

| Parameter | Location | Type | Required | Default / description |
| --- | --- | --- | --- | --- |
| `project_id` | query | string | yes |  |
| `limit` | query | integer | no | Default `100`.  |
| `offset` | query | integer | no | Default `0`.  |

Success `200`: `application/json`: `FigureRecord[]`.

Known domain failures: `404` NOT_FOUND.

#### `POST /api/figures`

ResourceCreate supplies project_id/title/data. Create the record and emit artifact_available. This creates editable metadata; it does not execute the related scientific task.

Body: `application/json`: `ResourceCreate`; required.

Success `200`: `application/json`: `FigureRecord`.

Known domain failures: `404` NOT_FOUND.

#### `DELETE /api/figures/{ident}`

Delete the metadata record and mark consuming materials stale; this handler does not delete all associated workspace files. Return deleted identifier.

| Parameter | Location | Type | Required | Default / description |
| --- | --- | --- | --- | --- |
| `ident` | path | string | yes |  |

Success `200`: `application/json`: `Deleted`.

Known domain failures: `404` NOT_FOUND.

#### `GET /api/figures/{ident}`

Return the persisted resource; data structure depends on the resource kind.

| Parameter | Location | Type | Required | Default / description |
| --- | --- | --- | --- | --- |
| `ident` | path | string | yes |  |

Success `200`: `application/json`: `FigureRecord`.

Known domain failures: `404` NOT_FOUND.

#### `PATCH /api/figures/{ident}`

Optional expected_revision compares this RESOURCE revision, default current. Replace supplied title/status; shallow-merge data unless replace_data:true, which replaces the complete data object. Ignore other top-level keys. Increment resource revision and mark consuming materials stale. For figures, changes to render inputs (kind/style/code/code_origin/run_ids/metric/data/caption/purpose/image_prompt/narrative_mode/image_variants/candidates) clear old render outputs and review selection and set needs_review; render and review the new revision before insertion.

| Parameter | Location | Type | Required | Default / description |
| --- | --- | --- | --- | --- |
| `ident` | path | string | yes |  |

Body: `application/json`: `ResourcePatch`; required.

Success `200`: `application/json`: `FigureRecord`.

Known domain failures: `404` NOT_FOUND; `409` REVISION_CONFLICT.

#### `GET /api/ideas`

Require query project_id. Return a bare resource array ordered by updated_at descending; limit capped at 500, offset clamped at zero. Kind-specific data is extensible.

| Parameter | Location | Type | Required | Default / description |
| --- | --- | --- | --- | --- |
| `project_id` | query | string | yes |  |
| `limit` | query | integer | no | Default `100`.  |
| `offset` | query | integer | no | Default `0`.  |

Success `200`: `application/json`: `RecordItem[]`.

Known domain failures: `404` NOT_FOUND.

#### `POST /api/ideas`

ResourceCreate supplies project_id/title/data. Create the record and emit artifact_available. This creates editable metadata; it does not execute the related scientific task.

Body: `application/json`: `ResourceCreate`; required.

Success `200`: `application/json`: `RecordItem`.

Known domain failures: `404` NOT_FOUND.

#### `DELETE /api/ideas/{ident}`

Delete the metadata record and mark consuming materials stale; this handler does not delete all associated workspace files. Return deleted identifier.

| Parameter | Location | Type | Required | Default / description |
| --- | --- | --- | --- | --- |
| `ident` | path | string | yes |  |

Success `200`: `application/json`: `Deleted`.

Known domain failures: `404` NOT_FOUND.

#### `GET /api/ideas/{ident}`

Return the persisted resource; data structure depends on the resource kind.

| Parameter | Location | Type | Required | Default / description |
| --- | --- | --- | --- | --- |
| `ident` | path | string | yes |  |

Success `200`: `application/json`: `RecordItem`.

Known domain failures: `404` NOT_FOUND.

#### `PATCH /api/ideas/{ident}`

Optional expected_revision compares this RESOURCE revision, default current. Replace supplied title/status; shallow-merge data unless replace_data:true, which replaces the complete data object. Ignore other top-level keys. Increment resource revision and mark consuming materials stale.

| Parameter | Location | Type | Required | Default / description |
| --- | --- | --- | --- | --- |
| `ident` | path | string | yes |  |

Body: `application/json`: `ResourcePatch`; required.

Success `200`: `application/json`: `RecordItem`.

Known domain failures: `404` NOT_FOUND; `409` REVISION_CONFLICT.

#### `GET /api/library`

Require query project_id. Return a bare resource array ordered by updated_at descending; limit capped at 500, offset clamped at zero. Kind-specific data is extensible.

| Parameter | Location | Type | Required | Default / description |
| --- | --- | --- | --- | --- |
| `project_id` | query | string | yes |  |
| `limit` | query | integer | no | Default `100`.  |
| `offset` | query | integer | no | Default `0`.  |

Success `200`: `application/json`: `RecordItem[]`.

Known domain failures: `404` NOT_FOUND.

#### `POST /api/library`

ResourceCreate supplies project_id/title/data. Create the record and emit artifact_available. This creates editable metadata; it does not execute the related scientific task.

Body: `application/json`: `ResourceCreate`; required.

Success `200`: `application/json`: `RecordItem`.

Known domain failures: `404` NOT_FOUND.

#### `DELETE /api/library/{ident}`

Delete the metadata record and mark consuming materials stale; this handler does not delete all associated workspace files. Return deleted identifier.

| Parameter | Location | Type | Required | Default / description |
| --- | --- | --- | --- | --- |
| `ident` | path | string | yes |  |

Success `200`: `application/json`: `Deleted`.

Known domain failures: `404` NOT_FOUND.

#### `GET /api/library/{ident}`

Return the persisted resource; data structure depends on the resource kind.

| Parameter | Location | Type | Required | Default / description |
| --- | --- | --- | --- | --- |
| `ident` | path | string | yes |  |

Success `200`: `application/json`: `RecordItem`.

Known domain failures: `404` NOT_FOUND.

#### `PATCH /api/library/{ident}`

Optional expected_revision compares this RESOURCE revision, default current. Replace supplied title/status; shallow-merge data unless replace_data:true, which replaces the complete data object. Ignore other top-level keys. Increment resource revision and mark consuming materials stale.

| Parameter | Location | Type | Required | Default / description |
| --- | --- | --- | --- | --- |
| `ident` | path | string | yes |  |

Body: `application/json`: `ResourcePatch`; required.

Success `200`: `application/json`: `RecordItem`.

Known domain failures: `404` NOT_FOUND; `409` REVISION_CONFLICT.

#### `GET /api/reviews`

Require query project_id. Return a bare resource array ordered by updated_at descending; limit capped at 500, offset clamped at zero. Kind-specific data is extensible.

| Parameter | Location | Type | Required | Default / description |
| --- | --- | --- | --- | --- |
| `project_id` | query | string | yes |  |
| `limit` | query | integer | no | Default `100`.  |
| `offset` | query | integer | no | Default `0`.  |

Success `200`: `application/json`: `ReviewRecord[]`.

Known domain failures: `404` NOT_FOUND.

#### `DELETE /api/reviews/{ident}`

Delete the metadata record and mark consuming materials stale; this handler does not delete all associated workspace files. Return deleted identifier.

| Parameter | Location | Type | Required | Default / description |
| --- | --- | --- | --- | --- |
| `ident` | path | string | yes |  |

Success `200`: `application/json`: `Deleted`.

Known domain failures: `404` NOT_FOUND.

#### `GET /api/reviews/{ident}`

Return the persisted resource; data structure depends on the resource kind.

| Parameter | Location | Type | Required | Default / description |
| --- | --- | --- | --- | --- |
| `ident` | path | string | yes |  |

Success `200`: `application/json`: `ReviewRecord`.

Known domain failures: `404` NOT_FOUND.

#### `PATCH /api/reviews/{ident}`

Optional expected_revision compares this RESOURCE revision, default current. Replace supplied title/status; shallow-merge data unless replace_data:true, which replaces the complete data object. Ignore other top-level keys. Increment resource revision and mark consuming materials stale.

| Parameter | Location | Type | Required | Default / description |
| --- | --- | --- | --- | --- |
| `ident` | path | string | yes |  |

Body: `application/json`: `ResourcePatch`; required.

Success `200`: `application/json`: `ReviewRecord`.

Known domain failures: `404` NOT_FOUND; `409` REVISION_CONFLICT.

#### `GET /api/theories`

Require query project_id. Return a bare resource array ordered by updated_at descending; limit capped at 500, offset clamped at zero. Kind-specific data is extensible.

| Parameter | Location | Type | Required | Default / description |
| --- | --- | --- | --- | --- |
| `project_id` | query | string | yes |  |
| `limit` | query | integer | no | Default `100`.  |
| `offset` | query | integer | no | Default `0`.  |

Success `200`: `application/json`: `RecordItem[]`.

Known domain failures: `404` NOT_FOUND.

#### `POST /api/theories`

ResourceCreate supplies project_id/title/data. Create the record and emit artifact_available. This creates editable metadata; it does not execute the related scientific task.

Body: `application/json`: `ResourceCreate`; required.

Success `200`: `application/json`: `RecordItem`.

Known domain failures: `404` NOT_FOUND.

#### `DELETE /api/theories/{ident}`

Delete the metadata record and mark consuming materials stale; this handler does not delete all associated workspace files. Return deleted identifier.

| Parameter | Location | Type | Required | Default / description |
| --- | --- | --- | --- | --- |
| `ident` | path | string | yes |  |

Success `200`: `application/json`: `Deleted`.

Known domain failures: `404` NOT_FOUND.

#### `GET /api/theories/{ident}`

Return the persisted resource; data structure depends on the resource kind.

| Parameter | Location | Type | Required | Default / description |
| --- | --- | --- | --- | --- |
| `ident` | path | string | yes |  |

Success `200`: `application/json`: `RecordItem`.

Known domain failures: `404` NOT_FOUND.

#### `PATCH /api/theories/{ident}`

Optional expected_revision compares this RESOURCE revision, default current. Replace supplied title/status; shallow-merge data unless replace_data:true, which replaces the complete data object. Ignore other top-level keys. Increment resource revision and mark consuming materials stale.

| Parameter | Location | Type | Required | Default / description |
| --- | --- | --- | --- | --- |
| `ident` | path | string | yes |  |

Body: `application/json`: `ResourcePatch`; required.

Success `200`: `application/json`: `RecordItem`.

Known domain failures: `404` NOT_FOUND; `409` REVISION_CONFLICT.

### Runs

#### `POST /api/branches/{ident}/run`

Choose the branch root (or first unarchived node) and enqueue its descendants. RunRequest.scope is accepted but the handler uses descendants. Return {runs}; there is no guaranteed run_ids field.

| Parameter | Location | Type | Required | Default / description |
| --- | --- | --- | --- | --- |
| `ident` | path | string | yes |  |

Body: `application/json`: `RunRequest`; required.

Success `200`: `application/json`: `RunCollection`.

Known domain failures: `400` EMPTY_BRANCH or scheduling/input errors; `404` NOT_FOUND.

#### `POST /api/nodes/{ident}/run`

Use RunRequest scope/config/request_id. A single scheduled run returns a Run directly; any other count returns {runs,run_ids}. Resolve this union before reading status/id. Provider-backed runs in imported projects with an unavailable archived provider require an explicit provider or workspace-default selection before scheduling.

| Parameter | Location | Type | Required | Default / description |
| --- | --- | --- | --- | --- |
| `ident` | path | string | yes |  |

Body: `application/json`: `RunRequest`; required.

Success `200`: `application/json`: `Run or SelectedRunCollection`.

Known domain failures: `400` Scheduling/input errors; `404` NOT_FOUND; `409` Submission/revision conflicts; `422` PROVIDER_SELECTION_REQUIRED.

#### `GET /api/projects/{ident}/runs`

Bare Run array, created_at descending; limit capped at 500. include_manuscript_evidence=true adds a readiness result based on each completed experiment/command/agent run's readable numeric metrics artifact.

| Parameter | Location | Type | Required | Default / description |
| --- | --- | --- | --- | --- |
| `ident` | path | string | yes |  |
| `limit` | query | integer | no | Default `100`. Maximum number of runs, capped at 500. |
| `offset` | query | integer | no | Default `0`. Number of runs to skip. |
| `include_manuscript_evidence` | query | boolean | no | Default `False`. Attach manuscript evidence readiness and validation details. |

Success `200`: `application/json`: `Run[]`.

Known domain failures: `404` NOT_FOUND.

#### `POST /api/projects/{ident}/runs/selected`

node_ids must belong to this project. Pass optional request_id/config to the scheduler and return {runs,run_ids}. Empty selection can return empty arrays; inspect scheduling failures as errors.

| Parameter | Location | Type | Required | Default / description |
| --- | --- | --- | --- | --- |
| `ident` | path | string | yes |  |

Body: `application/json`: `SelectedRunsRequest`; required.

Success `200`: `application/json`: `SelectedRunCollection`.

Known domain failures: `400` CROSS_PROJECT or scheduling/input errors; `404` NOT_FOUND.

#### `GET /api/runs/{ident}`

Return RunDetail. Remove config.provider_snapshot and attach tools ordered by created_at; other task-specific configuration keys remain as saved.

| Parameter | Location | Type | Required | Default / description |
| --- | --- | --- | --- | --- |
| `ident` | path | string | yes |  |

Success `200`: `application/json`: `RunDetail`.

Known domain failures: `404` NOT_FOUND.

#### `PATCH /api/runs/{ident}/checkpoint`

Require waiting_input run with resource.checkpoint. Set checkpoint.override to body.payload (omission sets null). Return checkpoint/status; this does not resume execution. Resume separately via the run action endpoint.

| Parameter | Location | Type | Required | Default / description |
| --- | --- | --- | --- | --- |
| `ident` | path | string | yes |  |

Body: `application/json`: `CheckpointRequest`; required.

Success `200`: `application/json`: `CheckpointResult`.

Known domain failures: `404` NOT_FOUND; `409` NO_CHECKPOINT.

#### `PATCH /api/runs/{ident}/configuration`

Shallow-merge task parameters; reject service-managed provider_snapshot/execution_attempt/resolved_inputs/project_goal/repository-source/verification-binding/result/analysis-snapshot identities. For research_analysis only budget, agent_budget, timeout and priority are editable; changed question/scope/literature/provider requires a new analysis intent. Validate supplied repository for eligible run kinds. Scientific changes after execution mark verification unverified. Return config with provider_snapshot/env omitted. No revision guard; unknown editable keys remain extensible for other kinds.

| Parameter | Location | Type | Required | Default / description |
| --- | --- | --- | --- | --- |
| `ident` | path | string | yes |  |

Body: `application/json`: `JsonObject`; required.

Success `200`: `application/json`: `RunConfiguration`.

Known domain failures: `404` NOT_FOUND; `409` ANALYSIS_INTENT_BOUND; `422` INVALID_CONFIGURATION or INVALID_REPOSITORY.

#### `GET /api/runs/{ident}/diagnostics`

Return bounded/redacted saved local/container/remote receipts, log size/update time and source provenance. Only valid local process identities can add process_alive; remote/container status is the last saved observation and remote_state_is_live=false. This endpoint does not start Git/Docker/SSH/model operations.

| Parameter | Location | Type | Required | Default / description |
| --- | --- | --- | --- | --- |
| `ident` | path | string | yes |  |

Success `200`: `application/json`: `RunDiagnostics`.

Known domain failures: `404` NOT_FOUND.

#### `GET /api/runs/{ident}/lineage`

Traverse same-project run dependencies. Available rows include config (without provider_snapshot/env), metrics, files and comparisons between saved workspace and current branch working files. Missing/cross-project dependencies are {id,missing:true}. current describes node revision/result flags, not semantic correctness.

| Parameter | Location | Type | Required | Default / description |
| --- | --- | --- | --- | --- |
| `ident` | path | string | yes |  |

Success `200`: `application/json`: `RunLineage`.

Known domain failures: `404` NOT_FOUND.

#### `GET /api/runs/{ident}/output`

Read stdout.txt at max(offset,0), at most min(limit,1000000) bytes and return the next byte offset. A trailing multi-byte UTF-8 character cut by the page limit is held back so the next page re-reads it whole; when the page limit is smaller than the next character the read grows by at most three bytes so the cursor still advances; invalid bytes decode with replacement. Optional case-insensitive search filters lines AFTER the cursor advances. A missing output file returns empty text at the requested nonnegative offset.

| Parameter | Location | Type | Required | Default / description |
| --- | --- | --- | --- | --- |
| `ident` | path | string | yes |  |
| `offset` | query | integer | no | Default `0`. Byte offset, not line number. |
| `limit` | query | integer | no | Default `100000`. Maximum bytes; default 100000, capped at 1000000. |
| `search` | query | string | no | Default ``. Case-insensitive literal filter on the already-read lines. |

Success `200`: `application/json`: `OutputPage`.

Known domain failures: `404` NOT_FOUND.

#### `GET /api/runs/{ident}/session`

Return {run_id,status,session}. Missing agent_session.json means session:null. Full session is extensible scientific state and excludes cumulative resource accounting. For a cancelled run, session status follows the authoritative cancelled run. summary=true includes lifecycle, round, recovery, goal_contract, status/wait_for/updated_at/budget_reason/reconciliation_reason and transcript_count; selected fields can be null for legacy checkpoints. summary usage reads totals/active_seconds/completeness explicitly from the separate matching agent_usage.json resource ledger, or is null when unavailable. Invalid or unreadable ledgers return usage:null with usage_error diagnostics, never zero consumption. waiting with wait_for.type=work_round is automatic checkpoint continuation under the same Run ID and resource ledger.

| Parameter | Location | Type | Required | Default / description |
| --- | --- | --- | --- | --- |
| `ident` | path | string | yes |  |
| `summary` | query | boolean | no | Default `False`.  |

Success `200`: `application/json`: `RunSession`.

Known domain failures: `404` NOT_FOUND.

#### `POST /api/runs/{ident}/{action}`

Actions: cancel, pause, resume, retry, skip, priority. retry requires a terminal run and returns a NEW Run. cancel is idempotent for terminal runs. pause accepts queued/waiting/budget_exhausted/running; resume accepts paused/waiting_input/waiting/budget_exhausted plus the documented legacy Agent context failure. skip accepts queued only. priority converts body.priority with int(). Resume can merge Agent budget and context_char_budget (integer >=1000). Return the persisted Run; process actions can fail with 409.

| Parameter | Location | Type | Required | Default / description |
| --- | --- | --- | --- | --- |
| `ident` | path | string | yes |  |
| `action` | path | string | yes |  Values: cancel, pause, resume, retry, skip, priority. |

Body: `application/json`: `RunActionRequest`; optional.

Success `200`: `application/json`: `Run`.

Known domain failures: `404` NOT_FOUND or UNKNOWN_ACTION; `409` RUN_ACTIVE, INVALID_RUN_STATE or PROCESS_UNAVAILABLE; `422` INVALID_RUN_KIND, INVALID_CONTEXT_BUDGET or INVALID_AGENT_BUDGET.

### Sharing

#### `POST /api/projects/{ident}/share`

Store the body as share selection and return id, relative /share/<token> URL and token. node_ids determines visible nodes; description opts into the project description. Default selection exposes no nodes.

| Parameter | Location | Type | Required | Default / description |
| --- | --- | --- | --- | --- |
| `ident` | path | string | yes |  |

Body: `application/json`: `ShareRequest`; optional.

Success `200`: `application/json`: `ShareCreated`.

Known domain failures: `404` NOT_FOUND.

#### `DELETE /api/shares/{token}`

Require an already-valid owner cookie or bearer token even on trusted loopback; the public-prefix middleware shortcut does not authorize revocation. Disable the share and return revoked:true.

| Parameter | Location | Type | Required | Default / description |
| --- | --- | --- | --- | --- |
| `token` | path | string | yes |  |

Success `200`: `application/json`: `object`.

Known domain failures: `401` UNAUTHORIZED; `404` NOT_FOUND.

#### `GET /api/shares/{token}`

Public token URL. Return only selected node display/status fields and project name/optional description. It does not expose node instructions, files, graph edges, runs or credentials.

| Parameter | Location | Type | Required | Default / description |
| --- | --- | --- | --- | --- |
| `token` | path | string | yes |  |

Success `200`: `application/json`: `ShareSnapshot`.

Known domain failures: `404` NOT_FOUND.

### System

#### `GET /api/health`

Execute a database SELECT and return status, dialect and API version. This endpoint is public.

Success `200`: `application/json`: `Health`.

#### `GET /api/system`

Return CPU/memory/disk/GPU and worker heartbeat views. GPU counters are strings from nvidia-smi; latex is an executable path or null. Provider availability checks model-list transport (15-second cache), distinct from the saved chat test. No scientific run is launched.

Success `200`: `application/json`: `SystemState`.

### Verification

#### `GET /api/runs/{ident}/verification`

For a verification run, return its current checks/verdict; for a producer run, return current linked verifier verdicts, accepted check paths and numerical scope. Configuration/artifact/node changes can invalidate prior acceptance. Status is independent of execution completion.

| Parameter | Location | Type | Required | Default / description |
| --- | --- | --- | --- | --- |
| `ident` | path | string | yes |  |

Success `200`: `application/json`: `Verification`.

Known domain failures: `404` NOT_FOUND.

#### `POST /api/verification/run`

Require string project_id. Optional node_id must be a verification-kind node in that project. Exclude project_id/node_id/request_id from task config and reject reserved service-owned identity/verdict fields. Reused request_id must have the same verifier/node/contract. Return Run with provider_snapshot/env/remote config entries omitted.

Body: `application/json`: `VerificationRequest`; required.

Success `200`: `application/json`: `Run`.

Known domain failures: `404` NOT_FOUND; `409` REQUEST_ID_CONFLICT; `422` INVALID_VERIFICATION or INVALID_CONFIGURATION.

## Request and response field definitions

Fields marked optional may be absent. A nullable field may be present with
`null`; absence and null are separate states. Extensible objects admit
additional scientific/configuration keys. Timestamps are ordinary strings
because saved receipts do not all use one strict RFC 3339 format.

### `AcceptanceView`

| Field | Type | Required | Details |
| --- | --- | --- | --- |
| `ready` | boolean | yes |  |
| `purpose` | string | yes |  |
| `scope` | string | yes |  |
| `contract` | object or null | no |  |
| `failures` | string[] | no |  |
| `source_checks` | object[] | no |  |
| `comparison` | object or null | no |  |
| `confirmations` | object[] | no |  |

Additional properties: extensible JSON.

### `Agent`

| Field | Type | Required | Details |
| --- | --- | --- | --- |
| `id` | string | yes | Record identifier. |
| `created_at` | string | no | Creation timestamp, normally UTC ISO 8601. |
| `updated_at` | string | no | Last database update timestamp, normally UTC ISO 8601. |
| `name` | string | yes |  |
| `role` | string | yes |  |
| `instructions` | string | yes |  |
| `provider_id` | string or null | yes |  |
| `tools` | JsonValue[] | yes |  |
| `config` | JsonObject | yes |  |
| `enabled` | boolean | yes |  |

Additional properties: extensible JSON.

### `AgentCreate`

| Field | Type | Required | Details |
| --- | --- | --- | --- |
| `name` | string | yes |  |
| `role` | string | yes |  |
| `instructions` | string | no |  |
| `provider_id` | string or null | no |  |
| `tools` | JsonValue[] | no |  |
| `config` | JsonObject | no |  |
| `enabled` | boolean | no |  |

Additional properties: extensible JSON.

### `AgentWrite`

| Field | Type | Required | Details |
| --- | --- | --- | --- |
| `name` | string | no |  |
| `role` | string | no |  |
| `instructions` | string | no |  |
| `provider_id` | string or null | no |  |
| `tools` | JsonValue[] | no |  |
| `config` | JsonObject | no |  |
| `enabled` | boolean | no |  |

Additional properties: extensible JSON.

### `AnalysisRequest`

| Field | Type | Required | Details |
| --- | --- | --- | --- |
| `request_id` | string | yes |  minLength=1, maxLength=120. |
| `expected_revision` | integer | yes |  minimum=0.0. |
| `expected_fingerprint` | string or null | no |  |
| `scope` | string | no |  Default: `project`. Values: `project`, `node`, `branch`. |
| `node_id` | string or null | no |  |
| `branch_id` | string or null | no |  |
| `question` | string | no |  Default: ``. maxLength=16000. |
| `language` | string | no |  Default: `en`. Values: `zh`, `en`. |
| `literature_query` | string | no |  Default: ``. maxLength=1000. |
| `literature_sources` | string[] | no |  |
| `literature_limit` | integer | no |  Default: `6`. minimum=1.0, maximum=12.0. |
| `timeout` | number | no |  Default: `180`. maximum=3600.0. |
| `provider_id` | string or null | no |  |

Additional properties: rejected by the existing typed/schema-specific validator.

### `ApiError`

FastAPI validation failures have an array detail; application failures normally have an object detail. Preserve both shapes.

| Field | Type | Required | Details |
| --- | --- | --- | --- |
| `detail` | ApiErrorDetail or string or object[] | yes |  |

Additional properties: extensible JSON.

### `ApiErrorDetail`

Application errors use detail.code/message/retryable. suggestion and context keys are optional; graph errors can flatten their context into detail.

| Field | Type | Required | Details |
| --- | --- | --- | --- |
| `code` | string | yes |  |
| `message` | string | yes |  |
| `retryable` | boolean | yes |  |
| `suggestion` | string | no |  |
| `details` | JsonObject | no |  |
| `expected_revision` | integer | no |  |
| `current_revision` | integer | no |  |

Additional properties: extensible JSON.

### `ApplicabilityDecision`

| Field | Type | Required | Details |
| --- | --- | --- | --- |
| `request_id` | string | yes |  minLength=1, maxLength=160. |
| `expected_revision` | integer | yes |  minimum=0.0. |
| `choice` | string | yes |  Values: `reuse`, `exclude`. |
| `reason` | string | yes |  minLength=1, maxLength=100000. |

Additional properties: rejected by the existing typed/schema-specific validator.

### `ApplicabilityView`

| Field | Type | Required | Details |
| --- | --- | --- | --- |
| `run_id` | string | yes |  |
| `status` | string | yes |  Values: `current`, `approved`, `excluded`, `needs_review`, `unknown`. |
| `ready` | boolean | yes |  |
| `current_goal` | string | yes |  |
| `execution_goals` | string[] | yes |  |
| `current_goal_scope` | object | yes |  |
| `execution_goal_scopes` | object[] | yes |  |
| `decision_id` | string or null | yes |  |
| `reason` | string | yes |  |
| `scope` | string | yes |  |

Additional properties: extensible JSON.

### `AuthLoginRequest`

| Field | Type | Required | Details |
| --- | --- | --- | --- |
| `token` | string | yes |  |

Additional properties: extensible JSON.

### `Authenticated`

| Field | Type | Required | Details |
| --- | --- | --- | --- |
| `authenticated` | boolean | yes |  |

Additional properties: extensible JSON.

### `Body_import_project_api_projects_import_post`

| Field | Type | Required | Details |
| --- | --- | --- | --- |
| `file` | string | yes |  |

Additional properties: extensible JSON.

### `Body_upload_api_projects__ident__upload_post`

| Field | Type | Required | Details |
| --- | --- | --- | --- |
| `file` | string | yes |  |

Additional properties: extensible JSON.

### `Branch`

Editable branch/workspace. Graph command snapshots may lack database timestamps or project_id; persisted branch rows contain them. Additional branch metadata is flattened.

| Field | Type | Required | Details |
| --- | --- | --- | --- |
| `id` | string | yes | Record identifier. |
| `created_at` | string | no | Creation timestamp, normally UTC ISO 8601. |
| `updated_at` | string | no | Last database update timestamp, normally UTC ISO 8601. |
| `project_id` | string | no |  |
| `name` | string | yes |  |
| `root_node_id` | string or null | no |  |
| `status` | string | yes |  |
| `workspace` | string | yes |  |
| `is_main` | boolean | yes |  |
| `config` | JsonObject | yes |  |

Additional properties: extensible JSON.

### `BrowserReadRequest`

| Field | Type | Required | Details |
| --- | --- | --- | --- |
| `project_id` | string | yes |  |
| `url` | string | yes |  |
| `screenshot` | boolean | no |  |

Additional properties: extensible JSON.

### `CheckpointRequest`

| Field | Type | Required | Details |
| --- | --- | --- | --- |
| `payload` | JsonValue | no |  |

Additional properties: extensible JSON.

### `CheckpointResult`

| Field | Type | Required | Details |
| --- | --- | --- | --- |
| `checkpoint` | JsonObject | yes |  |
| `status` | string | yes |  |

Additional properties: extensible JSON.

### `CleanupRequest`

| Field | Type | Required | Details |
| --- | --- | --- | --- |
| `days` | integer | no | Terminal runs older than this many days; int conversion, clamped at zero; default 30. |
| `project_id` | string | no | Optional restriction for run cleanup. |
| `clear_edit_history` | boolean | no | Clears graph edit history for ALL projects, independently of project_id. |

Additional properties: extensible JSON.

### `CleanupResult`

| Field | Type | Required | Details |
| --- | --- | --- | --- |
| `deleted_runs` | integer | yes |  |

Additional properties: extensible JSON.

### `ComparedRun`

| Field | Type | Required | Details |
| --- | --- | --- | --- |
| `id` | string | yes |  |
| `status` | string | yes |  |
| `metrics` | JsonObject | yes |  |
| `resource` | JsonObject | yes |  |
| `config` | JsonObject | yes |  |

Additional properties: extensible JSON.

### `ComparisonDeclaration`

| Field | Type | Required | Details |
| --- | --- | --- | --- |
| `run_id` | string | yes |  |
| `complete` | boolean | yes |  |
| `signature` | JsonObject | yes |  |
| `missing_fields` | string[] | yes |  |
| `conflicting_fields` | string[] | yes |  |
| `scope` | string | yes |  |

Additional properties: extensible JSON.

### `ContextSnapshot`

| Field | Type | Required | Details |
| --- | --- | --- | --- |
| `project_id` | string or null | yes |  |
| `node_id` | string | yes |  |
| `branch_id` | string | yes |  |
| `role` | string | yes |  |
| `graph_revision` | integer | yes |  |
| `node_revision` | integer | yes |  |
| `controls` | JsonObject | yes |  |
| `materials` | JsonObject[] | yes |  |
| `retrievable_materials` | JsonObject[] | yes |  |
| `omitted` | JsonObject[] | yes |  |
| `summaries_stale` | string[] | yes |  |
| `imported_branches` | string[] | yes |  |
| `overrides` | JsonObject | yes |  |
| `capacity` | object | yes |  |
| `text` | string | yes |  |

Additional properties: extensible JSON.

### `DataRows`

| Field | Type | Required | Details |
| --- | --- | --- | --- |
| `columns` | string[] | yes |  |
| `rows` | JsonObject[] | yes |  |
| `total` | integer | yes |  |
| `origin` | string | yes |  |

Additional properties: extensible JSON.

### `DecisionAnswer`

| Field | Type | Required | Details |
| --- | --- | --- | --- |
| `expected_revision` | integer | yes |  minimum=0.0. |
| `choice` | string | yes |  Values: `accept`, `edit`, `reject`. |
| `reason` | string | no |  Default: ``. maxLength=100000. |
| `action` | object or null | no |  |
| `resume` | boolean | no |  Default: `True`. |

Additional properties: rejected by the existing typed/schema-specific validator.

### `DecisionView`

| Field | Type | Required | Details |
| --- | --- | --- | --- |
| `id` | string | yes |  |
| `project_id` | string | yes |  |
| `run_id` | string | yes |  |
| `action_id` | string | yes |  |
| `attempt_id` | string or null | yes |  |
| `observed_revision` | integer | yes |  |
| `proposed` | object | yes |  |
| `status` | string | yes |  |
| `answer` | object | yes |  |
| `answered_at` | string or null | yes |  |
| `consumed_at` | string or null | yes |  |
| `created_at` | string | yes |  |
| `updated_at` | string | yes |  |
| `resume_error` | string or null | no |  |
| `run_status` | string or null | no |  |
| `can_resume` | boolean or null | no |  |

Additional properties: extensible JSON.

### `Deleted`

| Field | Type | Required | Details |
| --- | --- | --- | --- |
| `deleted` | string | yes |  |

Additional properties: extensible JSON.

### `EffectView`

| Field | Type | Required | Details |
| --- | --- | --- | --- |
| `id` | string | yes |  |
| `intervention_id` | string | yes |  |
| `project_id` | string | yes |  |
| `run_id` | string | yes |  |
| `attempt_id` | string or null | yes |  |
| `action` | string | yes |  |
| `status` | string | yes |  |
| `observation` | object | yes |  |
| `error` | string or null | yes |  |
| `attempts` | integer | yes |  |
| `retry_after` | number | yes |  |
| `created_at` | string | yes |  |
| `updated_at` | string | yes |  |
| `applied_at` | string or null | yes |  |

Additional properties: extensible JSON.

### `EvaluationProtocolRevisionRequest`

| Field | Type | Required | Details |
| --- | --- | --- | --- |
| `request_id` | string | no |  |
| `expected_revision` | integer | no |  |
| `expected_protocol_revision` | integer | no |  |
| `protocol_id` | string | no |  |
| `name` | string | no |  |
| `definition` | JsonObject | yes |  |
| `reason` | string | no |  |
| `feedback_run_ids` | string[] | no |  |
| `classification` | string | no |  Values: `adaptive`, `exploratory`, `independent_confirmation`. |

Additional properties: extensible JSON.

### `EvaluationProtocolVersion`

| Field | Type | Required | Details |
| --- | --- | --- | --- |
| `id` | string | yes | Record identifier. |
| `created_at` | string | no | Creation timestamp, normally UTC ISO 8601. |
| `updated_at` | string | no | Last database update timestamp, normally UTC ISO 8601. |
| `project_id` | string | yes |  |
| `protocol_id` | string | yes |  |
| `revision` | integer | yes |  |
| `parent_version_id` | string or null | no |  |
| `name` | string | no |  |
| `definition` | JsonObject | yes |  |
| `reason` | string | no |  |
| `feedback_run_ids` | string[] | yes |  |
| `classification` | string | yes |  Values: `adaptive`, `exploratory`, `independent_confirmation`. |

Additional properties: extensible JSON.

### `ExperimentCompareRequest`

| Field | Type | Required | Details |
| --- | --- | --- | --- |
| `run_ids` | string[] | yes |  |
| `objective` | JsonObject | no |  |

Additional properties: extensible JSON.

### `ExperimentComparison`

| Field | Type | Required | Details |
| --- | --- | --- | --- |
| `runs` | ComparedRun[] | yes |  |
| `directly_comparable` | boolean | yes |  |
| `comparison_status` | string | yes |  Values: `directly_comparable`, `incomparable`, `unverified`. |
| `reason` | string | yes |  |
| `declarations` | ComparisonDeclaration[] | yes |  |
| `noncompleted_run_ids` | string[] | yes |  |
| `verification_scope` | string | yes |  |

Additional properties: extensible JSON.

### `FigureData`

Saved Figure data. Selection, scientific data, code, rendering style and artifact paths are separate editable fields. Ready status is on the enclosing resource; native editable render and whole-image raster output are different kinds.

| Field | Type | Required | Details |
| --- | --- | --- | --- |
| `kind` | string | no | Renderer type, including bar, line, heatmap, forest, calibration, method and image; actual supported settings depend on the renderer. |
| `run_ids` | string[] | no |  |
| `source_run_ids` | string[] | no |  |
| `metric` | string | no |  |
| `purpose` | string | no |  |
| `caption` | string | no |  |
| `style` | FigureStyle | no |  |
| `data` | JsonValue | no | Actual imported/derived plot measurements, calibration prediction rows, or a MethodDiagramSpec. Never replace numerical data with generated pixels. |
| `image_prompt` | string | no |  |
| `image_variants` | JsonValue[] | no |  |
| `candidates` | JsonObject[] | no |  |
| `narrative_mode` | string | no |  |
| `code` | string | no |  |
| `code_origin` | string | no |  |
| `outputs` | FigureOutputs | no |  |
| `visual_selection` | VisualSelection or null | no |  |
| `visual_review_status` | string | no |  |
| `svg_path` | string | no |  |
| `png_path` | string | no |  |

Additional properties: extensible JSON.

### `FigureOutputs`

Available output key to PROJECT-relative file path. A key is absent until that artifact exists; no format is universal.

| Field | Type | Required | Details |
| --- | --- | --- | --- |
| `svg` | string | no |  |
| `pdf` | string | no |  |
| `png` | string | no |  |
| `source` | string | no |  |
| `data` | string | no |  |
| `style` | string | no |  |
| `report` | string | no |  |
| `selection` | string | no |  |
| `caption_context` | string | no |  |
| `prompt` | string | no |  |

Additional properties: extensible JSON.

### `FigureRecord`

| Field | Type | Required | Details |
| --- | --- | --- | --- |
| `id` | string | yes | Record identifier. |
| `created_at` | string | yes | Creation timestamp, normally UTC ISO 8601. |
| `updated_at` | string | yes | Last database update timestamp, normally UTC ISO 8601. |
| `project_id` | string | yes |  |
| `title` | string | yes |  |
| `revision` | integer | yes |  |
| `status` | string | yes |  |
| `data` | FigureData | yes |  |

Additional properties: extensible JSON.

### `FigureRegion`

Canvas selection with normalized top-left coordinates used by the current figure editor. This is descriptive task input, not a new runtime geometry validator.

| Field | Type | Required | Details |
| --- | --- | --- | --- |
| `x` | number | no |  |
| `y` | number | no |  |
| `width` | number | no |  |
| `height` | number | no |  |

Additional properties: extensible JSON.

### `FigureStyle`

Physical dimensions are inches and font_size is points. Renderer-specific plot/calibration/statistical style settings remain extensible.

| Field | Type | Required | Details |
| --- | --- | --- | --- |
| `title` | string | no |  |
| `xlabel` | string | no |  |
| `ylabel` | string | no |  |
| `color` | JsonValue | no |  |
| `width` | number | no |  |
| `layout_width_in` | number | no |  |
| `height` | number | no |  |
| `font_size` | number | no |  |
| `span` | string | no |  |
| `paper_layout` | PaperLayout | no |  |
| `paper_template` | string | no |  |
| `uncertainty` | JsonValue | no |  |
| `legend` | JsonValue | no |  |

Additional properties: extensible JSON.

### `FigureTaskRequest`

Revision work needs instruction. expected_revision, when supplied to /revise, compares the Figure resource revision. Image-candidate reuse refers to a real terminal figure run and retained candidate manifest. Other worker options remain extensible.

| Field | Type | Required | Details |
| --- | --- | --- | --- |
| `expected_revision` | integer | no |  |
| `request_id` | string or null | no |  |
| `instruction` | string | no |  |
| `region` | FigureRegion or null | no |  |
| `run_ids` | string[] | no |  |
| `provider_id` | string | no |  |
| `visual_review_attempts` | integer | no |  |
| `image_candidate_run_id` | string | no |  |
| `image_candidate_iteration` | integer | no |  |

Additional properties: extensible JSON.

### `FileEntry`

| Field | Type | Required | Details |
| --- | --- | --- | --- |
| `path` | string | yes |  |
| `size` | integer | yes |  |
| `modified` | number | yes |  |
| `is_dir` | boolean | yes |  |

Additional properties: extensible JSON.

### `FileExistenceRequest`

| Field | Type | Required | Details |
| --- | --- | --- | --- |
| `paths` | string[] | yes |  |

Additional properties: extensible JSON.

### `FileExistenceResult`

| Field | Type | Required | Details |
| --- | --- | --- | --- |
| `existing` | string[] | yes |  |
| `directories` | string[] | yes |  |

Additional properties: extensible JSON.

### `FileList`

| Field | Type | Required | Details |
| --- | --- | --- | --- |
| `files` | FileEntry[] | yes |  |
| `has_more` | boolean | yes |  |
| `next_cursor` | string or null | yes |  |

Additional properties: extensible JSON.

### `FileObservation`

| Field | Type | Required | Details |
| --- | --- | --- | --- |
| `id` | string | yes |  |
| `scope_id` | string | yes |  |
| `path` | string | yes |  |
| `generation` | integer | yes |  |
| `state` | string | yes |  |
| `size` | integer | yes |  |
| `parse_state` | string | yes |  |
| `attribution` | string | yes |  |
| `observed_at` | string or null | yes |  |
| `source` | SourceRef | yes |  |

Additional properties: rejected by the existing typed/schema-specific validator.

### `FilePage`

| Field | Type | Required | Details |
| --- | --- | --- | --- |
| `items` | FileObservation[] | yes |  |
| `next_cursor` | string or null | yes |  |
| `coverage` | ScopeCoverage | yes |  |

Additional properties: rejected by the existing typed/schema-specific validator.

### `FilePath`

| Field | Type | Required | Details |
| --- | --- | --- | --- |
| `path` | string | yes |  |

Additional properties: extensible JSON.

### `FileRead`

| Field | Type | Required | Details |
| --- | --- | --- | --- |
| `path` | string | yes |  |
| `content` | string | yes |  |
| `revision` | integer | yes |  |
| `origin` | string | yes |  |

Additional properties: extensible JSON.

### `FileRenameRequest`

| Field | Type | Required | Details |
| --- | --- | --- | --- |
| `path` | string | yes |  |
| `new_path` | string | yes |  |

Additional properties: extensible JSON.

### `FileUpload`

| Field | Type | Required | Details |
| --- | --- | --- | --- |
| `path` | string | yes |  |
| `size` | integer | yes |  |
| `origin` | string | yes |  |

Additional properties: extensible JSON.

### `FileWrite`

| Field | Type | Required | Details |
| --- | --- | --- | --- |
| `path` | string | yes |  minLength=1, maxLength=2000. |
| `content` | string | yes |  maxLength=10000000. |
| `expected_revision` | integer or null | no |  |
| `create_only` | boolean | no |  Default: `False`. |

Additional properties: extensible JSON.

### `FileWritten`

| Field | Type | Required | Details |
| --- | --- | --- | --- |
| `path` | string | yes |  |
| `revision` | integer | yes |  |
| `origin` | string | yes |  |

Additional properties: extensible JSON.

### `GoalContractHistory`

| Field | Type | Required | Details |
| --- | --- | --- | --- |
| `current` | GoalContractVersion | yes |  |
| `versions` | GoalContractVersion[] | yes |  |
| `history_endpoint` | string | yes |  |
| `rule` | string | yes |  |
| `project_revision` | integer | no |  |
| `intervention` | JsonObject | no |  |

Additional properties: extensible JSON.

### `GoalContractRevisionRequest`

| Field | Type | Required | Details |
| --- | --- | --- | --- |
| `request_id` | string | no |  |
| `expected_revision` | integer | no |  |
| `expected_goal_revision` | integer | no |  |
| `objective` | string | no |  |
| `success_criteria` | JsonValue[] | no |  |
| `contract` | JsonObject | no |  |
| `reason` | string | no |  |
| `evidence_run_ids` | string[] | no |  |
| `affected_branch_ids` | string[] | no |  |

Additional properties: extensible JSON.

### `GoalContractVersion`

| Field | Type | Required | Details |
| --- | --- | --- | --- |
| `id` | string | yes | Record identifier. |
| `created_at` | string | no | Creation timestamp, normally UTC ISO 8601. |
| `updated_at` | string | no | Last database update timestamp, normally UTC ISO 8601. |
| `project_id` | string | yes |  |
| `revision` | integer | yes |  |
| `parent_version_id` | string or null | no |  |
| `project_revision` | integer | no |  |
| `objective` | string | yes |  |
| `success_criteria` | JsonValue[] | yes |  |
| `contract` | JsonObject | yes |  |
| `reason` | string | yes |  |
| `changes` | JsonObject | no |  |
| `evidence_run_ids` | string[] | no |  |
| `affected_branch_ids` | string[] | no |  |

Additional properties: extensible JSON.

### `GraphBatchRequest`

| Field | Type | Required | Details |
| --- | --- | --- | --- |
| `request_id` | string | no | Optional idempotency key. Shared with graph command receipts in this project. |
| `expected_revision` | integer | yes |  |
| `commands` | JsonObject[] | yes |  |

Additional properties: extensible JSON.

### `GraphBatchResult`

| Field | Type | Required | Details |
| --- | --- | --- | --- |
| `revision` | integer | yes |  |
| `applied` | integer | yes |  |
| `node_count` | integer | yes |  |
| `edge_count` | integer | yes |  |

Additional properties: extensible JSON.

### `GraphCommand`

| Field | Type | Required | Details |
| --- | --- | --- | --- |
| `project_id` | string or null | no |  |
| `request_id` | string | yes |  minLength=1, maxLength=160. |
| `expected_revision` | integer | yes |  |
| `operation` | string | yes |  |
| `targets` | string[] | no |  |
| `params` | object | no |  |
| `run` | boolean | no |  Default: `False`. |

Additional properties: rejected by the existing typed/schema-specific validator.

### `GraphCommandResult`

| Field | Type | Required | Details |
| --- | --- | --- | --- |
| `revision` | integer | yes |  |
| `graph` | GraphState | yes |  |
| `impact` | GraphImpact | yes |  |
| `run_nodes` | string[] | yes |  |
| `run_ids` | string[] | yes |  |

Additional properties: extensible JSON.

### `GraphEdge`

Typed relation. depends_on and consumes participate in execution cycle checks. Snapshot edges need not contain database timestamps or project_id.

| Field | Type | Required | Details |
| --- | --- | --- | --- |
| `id` | string | yes | Record identifier. |
| `created_at` | string | no | Creation timestamp, normally UTC ISO 8601. |
| `updated_at` | string | no | Last database update timestamp, normally UTC ISO 8601. |
| `project_id` | string | no |  |
| `source` | string | yes |  |
| `target` | string | yes |  |
| `relation` | string | yes |  |
| `input_mapping` | JsonObject | no |  |

Additional properties: extensible JSON.

### `GraphImpact`

Selective consequences of an edit. Preview calculates this without persisting graph edits or workspace copies.

| Field | Type | Required | Details |
| --- | --- | --- | --- |
| `changed_nodes` | string[] | yes |  |
| `affected_nodes` | string[] | yes |  |
| `rerun_nodes` | string[] | yes |  |
| `refresh_nodes` | string[] | yes |  |
| `reasons` | JsonObject | yes |  |
| `categories` | JsonObject | yes |  |
| `files` | JsonObject[] | yes |  |
| `conflicts` | JsonObject[] | yes |  |
| `actions` | JsonObject[] | no |  |
| `input_bindings_required` | JsonObject[] | no |  |
| `undo_scope` | string | no |  |

Additional properties: extensible JSON.

### `GraphNode`

Type: `ResearchNode`.

### `GraphPage`

| Field | Type | Required | Details |
| --- | --- | --- | --- |
| `revision` | integer | yes |  |
| `offset` | integer | yes |  |
| `nodes` | ResearchNode[] | yes |  |
| `edges` | GraphEdge[] | yes |  |
| `next_offset` | integer or null | yes |  |

Additional properties: extensible JSON.

### `GraphState`

Public editable graph. Additional graph metadata is retained; private _history is excluded from graph-read and graph-command responses.

| Field | Type | Required | Details |
| --- | --- | --- | --- |
| `project_id` | string | yes |  |
| `revision` | integer | yes |  |
| `goal` | string | no |  |
| `budget` | JsonObject | no |  |
| `nodes` | ResearchNode[] | yes |  |
| `edges` | GraphEdge[] | yes |  |
| `branches` | Branch[] | yes |  |
| `groups` | JsonObject[] | no |  |

Additional properties: extensible JSON.

### `HTTPValidationError`

| Field | Type | Required | Details |
| --- | --- | --- | --- |
| `detail` | ValidationError[] | no |  |

Additional properties: extensible JSON.

### `Health`

| Field | Type | Required | Details |
| --- | --- | --- | --- |
| `status` | string | yes |  |
| `database` | string | yes |  |
| `version` | string | yes |  |

Additional properties: extensible JSON.

### `Host`

| Field | Type | Required | Details |
| --- | --- | --- | --- |
| `id` | string | yes | Record identifier. |
| `created_at` | string | no | Creation timestamp, normally UTC ISO 8601. |
| `updated_at` | string | no | Last database update timestamp, normally UTC ISO 8601. |
| `name` | string | yes |  |
| `kind` | string | yes |  |
| `status` | string | yes |  |
| `config` | JsonObject | yes |  |

Additional properties: extensible JSON.

### `HostCreate`

| Field | Type | Required | Details |
| --- | --- | --- | --- |
| `name` | string | yes |  |
| `kind` | string | no |  |
| `config` | JsonObject | no |  |

Additional properties: extensible JSON.

### `HostWrite`

| Field | Type | Required | Details |
| --- | --- | --- | --- |
| `name` | string | no |  |
| `kind` | string | no |  |
| `config` | JsonObject | no |  |

Additional properties: extensible JSON.

### `IdListRequest`

| Field | Type | Required | Details |
| --- | --- | --- | --- |
| `ids` | string[] | yes |  |

Additional properties: extensible JSON.

### `IdeaAdoptRequest`

| Field | Type | Required | Details |
| --- | --- | --- | --- |
| `expected_revision` | integer | no |  |
| `request_id` | string | no |  |
| `branch_id` | string | no |  |
| `title` | string | no |  |
| `instructions` | string | no |  |
| `config` | JsonObject | no |  |
| `inputs` | JsonValue[] | no |  |

Additional properties: extensible JSON.

### `InputReference`

Explicit input binding. File path is relative to the referenced source branch unless project_scope:true; a node-only reference can expose context without copying a file. destination is relative to the receiving run workspace. Typed kind/id can bind saved idea/paper/dataset/run/figure/analysis records. Preserve missing/rebinding/source revision metadata during path edits.

| Field | Type | Required | Details |
| --- | --- | --- | --- |
| `kind` | string | no |  |
| `id` | string | no |  |
| `project_id` | string | no |  |
| `node_id` | string | no |  |
| `branch_id` | string | no |  |
| `path` | string | no |  |
| `destination` | string | no |  |
| `project_scope` | boolean | no |  |
| `revision` | integer | no |  |
| `verification_node_id` | string | no |  |
| `available` | boolean | no |  |
| `missing` | boolean | no |  |
| `needs_rebinding` | boolean | no |  |

Additional properties: extensible JSON.

### `InstructionRequest`

| Field | Type | Required | Details |
| --- | --- | --- | --- |
| `request_id` | string | yes |  minLength=1, maxLength=160. |
| `expected_revision` | integer | yes |  minimum=0.0. |
| `text` | string | yes |  minLength=1, maxLength=100000. |
| `scope` | string | yes |  Values: `project`, `branch`, `node`, `run`. |
| `target_id` | string or null | no |  |
| `boundary` | string | no |  Default: `next_request`. Values: `next_request`, `next_run`. |

Additional properties: rejected by the existing typed/schema-specific validator.

### `InterventionView`

| Field | Type | Required | Details |
| --- | --- | --- | --- |
| `id` | string | yes |  |
| `project_id` | string | yes |  |
| `request_id` | string | yes |  |
| `actor` | string | yes |  |
| `kind` | string | yes |  |
| `observed_revision` | integer | yes |  |
| `applied_revision` | integer | yes |  |
| `intent` | object | yes |  |
| `impact` | object | yes |  |
| `status` | string | yes |  |
| `accepted_at` | string | yes |  |
| `applied_at` | string or null | yes |  |
| `created_at` | string | yes |  |
| `updated_at` | string | yes |  |
| `effects` | EffectView[] | yes |  |

Additional properties: extensible JSON.

### `JsonObject`

Extensible JSON object; unknown keys are retained where the endpoint merges this object.

Type: `object`.

### `JsonValue`

Any JSON value. Its structure is defined by the selected scientific tool or configuration.

Type: `any JSON`.

### `LibraryCompareResult`

| Field | Type | Required | Details |
| --- | --- | --- | --- |
| `papers` | object[] | yes |  |

Additional properties: extensible JSON.

### `LibraryImportRequest`

| Field | Type | Required | Details |
| --- | --- | --- | --- |
| `project_id` | string | yes |  |
| `identifier` | string | no |  |
| `paper` | JsonValue | no |  |

Additional properties: extensible JSON.

### `LibrarySearchRequest`

| Field | Type | Required | Details |
| --- | --- | --- | --- |
| `query` | string | yes |  |
| `source` | string | no |  |
| `limit` | integer | no |  |

Additional properties: extensible JSON.

### `LibrarySearchResult`

| Field | Type | Required | Details |
| --- | --- | --- | --- |
| `results` | JsonObject[] | yes |  |

Additional properties: extensible JSON.

### `LineageRun`

An available dependency has execution, source freshness and file data; an unavailable/cross-project dependency is only {id, missing:true}.

| Field | Type | Required | Details |
| --- | --- | --- | --- |
| `id` | string | yes |  |
| `missing` | boolean | no |  |
| `source_freshness` | JsonObject[] | no |  |
| `node_id` | string or null | no |  |
| `node_title` | string or null | no |  |
| `status` | string | no |  |
| `node_revision` | integer | no |  |
| `current_node_revision` | integer or null | no |  |
| `current` | boolean | no |  |
| `dependencies` | string[] | no |  |
| `config` | JsonObject | no |  |
| `metrics` | JsonObject | no |  |
| `resource` | JsonObject | no |  |
| `files` | object[] | no |  |

Additional properties: extensible JSON.

### `MethodDiagramSpec`

Scientific method topology and editable production scene/composition. nodes/edges use supplied operation IDs/labels; production_scene contains native panels/objects/connections/annotations. Renderer-specific fields remain editable through Figure data.

| Field | Type | Required | Details |
| --- | --- | --- | --- |
| `nodes` | JsonObject[] | no |  |
| `edges` | JsonObject[] | no |  |
| `storyboard` | JsonObject | no |  |
| `story_context` | JsonObject | no |  |
| `narrative_mode` | string | no |  |
| `key_operation_id` | string | no |  |
| `production_scene` | JsonObject | no |  |
| `production_contract` | JsonObject | no |  |
| `production_composition` | JsonObject | no |  |
| `production_raster` | JsonObject | no |  |
| `asset_data` | JsonObject | no |  |

Additional properties: extensible JSON.

### `NodePatch`

Passed to edit_node with optional project revision. Kernel-managed id/project_id/revision/execution_status/last_run_id/last_run_revision cannot be changed here. Other editable node metadata is extensible.

| Field | Type | Required | Details |
| --- | --- | --- | --- |
| `title` | string | no |  |
| `instructions` | string | no |  |
| `type` | string | no |  |
| `config` | JsonObject | no |  |
| `inputs` | JsonValue[] | no |  |
| `position` | JsonObject | no |  |
| `context_overrides` | JsonObject | no |  |
| `comments` | JsonValue[] | no |  |
| `archived` | boolean | no |  |
| `expected_revision` | integer | no | The PROJECT graph revision, not the node revision. |

Additional properties: extensible JSON.

### `ObjectiveRequest`

| Field | Type | Required | Details |
| --- | --- | --- | --- |
| `metric` | string | no | Blank/omitted metric clears the objective. |
| `direction` | string | no |  Default: `min`. Values: `min`, `max`. |
| `comparison_fields` | string[] | no |  |

Additional properties: extensible JSON.

### `ObserverHealth`

| Field | Type | Required | Details |
| --- | --- | --- | --- |
| `state` | string | yes |  Values: `healthy`, `rebuilding`, `stale`, `unavailable`. |
| `observed_at` | string or null | no |  |
| `age_seconds` | number or null | no |  |
| `database_coverage` | string | yes |  |
| `file_coverage` | string | yes |  |
| `history_gap` | boolean | no |  Default: `False`. |
| `note` | string | yes |  |

Additional properties: rejected by the existing typed/schema-specific validator.

### `OutputPage`

| Field | Type | Required | Details |
| --- | --- | --- | --- |
| `text` | string | yes |  |
| `offset` | integer | yes | Next byte cursor in stdout.txt, before any search filtering. |
| `status` | string | yes |  |

Additional properties: extensible JSON.

### `PairedStatisticsRequest`

| Field | Type | Required | Details |
| --- | --- | --- | --- |
| `path` | string | yes |  |
| `unit_column` | string | yes |  |
| `baseline_column` | string | yes |  |
| `candidate_column` | string | yes |  |
| `request_id` | string or null | no |  |
| `direction` | string | no |  |
| `confidence` | number | no |  |
| `bootstrap_samples` | integer | no |  |
| `seed` | integer | no |  |
| `meaningful_effect` | number | no |  |

Additional properties: extensible JSON.

### `PaperCheck`

| Field | Type | Required | Details |
| --- | --- | --- | --- |
| `issues` | JsonObject[] | yes |  |
| `writing_profile` | JsonObject | yes |  |
| `coverage` | string | yes |  |
| `status` | string | yes |  |

Additional properties: extensible JSON.

### `PaperData`

Manuscript source, real compilation artifacts and evidence/figure bindings. Most generated fields are absent in a new draft. Cleared layout/layout_plan/layout_preflight can be null. A PDF is current only when compiled_revision equals the enclosing PaperDocument revision.

| Field | Type | Required | Details |
| --- | --- | --- | --- |
| `source` | string | no |  |
| `bibtex` | string | no |  |
| `pdf_path` | string or null | no | Compiled PDF locator; null after failed or unavailable compilation. |
| `source_dir` | string | no |  |
| `log` | string | no |  |
| `errors` | JsonValue[] | no |  |
| `bindings` | JsonObject[] | no |  |
| `numeric_bindings` | JsonValue | no |  |
| `source_run_ids` | string[] | no |  |
| `figure_bindings` | JsonObject[] | no |  |
| `manually_edited` | boolean | no |  |
| `content_origin` | string | no |  |
| `compiled_revision` | integer | no |  |
| `template` | string | no |  |
| `layout` | PaperLayout or null | no |  |
| `layout_plan` | JsonObject or null | no |  |
| `layout_preflight` | JsonObject or null | no |  |
| `writing_profile` | JsonObject | no |  |
| `draft_reuse` | JsonObject | no |  |
| `accepted_review_id` | string | no |  |
| `stale_reason` | string | no |  |

Additional properties: extensible JSON.

### `PaperExportRequest`

| Field | Type | Required | Details |
| --- | --- | --- | --- |
| `expected_revision` | integer | no |  |
| `format` | string | no | pdf returns the current compiled PDF; other/omitted values return a source ZIP. |
| `source` | string | no |  |
| `bibtex` | string | no |  |

Additional properties: extensible JSON.

### `PaperFigureRequest`

| Field | Type | Required | Details |
| --- | --- | --- | --- |
| `expected_revision` | integer | yes |  |
| `figure_id` | string | yes |  |
| `anchor_text` | string | yes |  |
| `caption` | string | no |  |
| `span` | string | no |  |

Additional properties: extensible JSON.

### `PaperGenerationRequest`

| Field | Type | Required | Details |
| --- | --- | --- | --- |
| `run_ids` | string[] | yes |  |
| `request_id` | string or null | no |  |
| `manuscript_type` | string | no |  |
| `title` | string | no |  |
| `template` | string | no |  |
| `layout` | PaperLayout | no |  |
| `instructions` | string | no |  |
| `figure_ids` | string[] | no |  |
| `saved_response_run_id` | string | no |  |
| `saved_response_path` | string | no |  |
| `draft_revision_path` | string | no |  |

Additional properties: extensible JSON.

### `PaperLayout`

Existing normalized manuscript layout. table_font_pt must be >=min_font_pt. article allows one/two columns; iclr2027 requires single. Unknown layout keys are rejected by the layout worker/handler.

| Field | Type | Required | Details |
| --- | --- | --- | --- |
| `columns` | string | no |  Default: `single`. Values: `single`, `double`. |
| `significant_digits` | integer | no |  Default: `5`. minimum=2, maximum=10. |
| `scientific_notation` | string | no |  Default: `auto`. Values: `auto`, `always`, `never`. |
| `table_font_pt` | number | no |  Default: `9`. minimum=7, maximum=12. |
| `min_font_pt` | number | no |  Default: `8`. minimum=7, maximum=12. |
| `max_table_rows` | integer | no |  Default: `18`. minimum=4, maximum=60. |
| `float_placement` | string | no |  Default: `auto`. Values: `auto`, `top`, `bottom`, `page`, `here`. |
| `figure_span` | string | no |  Default: `auto`. Values: `auto`, `column`, `page`. |
| `table_span` | string | no |  Default: `auto`. Values: `auto`, `column`, `page`. |

Additional properties: rejected by the existing typed/schema-specific validator.

### `PaperLayoutRequest`

| Field | Type | Required | Details |
| --- | --- | --- | --- |
| `expected_revision` | integer | yes |  |
| `request_id` | string or null | no |  |
| `template` | string | no |  |
| `layout` | PaperLayout | no |  |

Additional properties: extensible JSON.

### `PaperPatch`

| Field | Type | Required | Details |
| --- | --- | --- | --- |
| `title` | string | no |  |
| `data` | JsonObject | no |  |
| `expected_revision` | integer | no |  |

Additional properties: extensible JSON.

### `PaperRecord`

| Field | Type | Required | Details |
| --- | --- | --- | --- |
| `id` | string | yes | Record identifier. |
| `created_at` | string | yes | Creation timestamp, normally UTC ISO 8601. |
| `updated_at` | string | yes | Last database update timestamp, normally UTC ISO 8601. |
| `project_id` | string | yes |  |
| `title` | string | yes |  |
| `revision` | integer | yes |  |
| `status` | string | yes |  |
| `data` | PaperData | yes |  |

Additional properties: extensible JSON.

### `PaperRevisionRequest`

| Field | Type | Required | Details |
| --- | --- | --- | --- |
| `expected_revision` | integer | no |  |
| `request_id` | string or null | no |  |

Additional properties: extensible JSON.

### `ProcessDiagnostic`

Optional receipt fields are returned only when present and scalar in the saved state. Commands may be a string, string array or null; local identity can add process_alive.

| Field | Type | Required | Details |
| --- | --- | --- | --- |
| `process_id` | string | yes |  |
| `backend` | string | yes |  |
| `status` | string | yes |  |
| `command` | JsonValue | yes |  |
| `state_source` | string | yes |  |
| `observed_at` | number or null | yes |  |
| `process_alive` | boolean | no |  |
| `exit_code` | JsonValue | no |  |
| `started_at` | JsonValue | no |  |
| `finished_at` | JsonValue | no |  |
| `elapsed_seconds` | JsonValue | no |  |

Additional properties: extensible JSON.

### `ProgressFact`

| Field | Type | Required | Details |
| --- | --- | --- | --- |
| `id` | string | yes |  |
| `section` | string | yes |  Values: `now`, `attention`, `blocked`, `recent`, `next`, `evidence`. |
| `label` | string | yes |  |
| `value` | string | yes |  |
| `classification` | string | no |  Default: `OPERATIONAL`. Values: `OPERATIONAL`, `REPORTED`. |
| `applicability` | string | no |  Default: `current`. Values: `current`, `historical`, `not_checked`. |
| `observed_at` | string | yes |  |
| `sources` | SourceRef[] | yes |  |

Additional properties: rejected by the existing typed/schema-specific validator.

### `Project`

| Field | Type | Required | Details |
| --- | --- | --- | --- |
| `id` | string | yes | Record identifier. |
| `created_at` | string | no | Creation timestamp, normally UTC ISO 8601. |
| `updated_at` | string | no | Last database update timestamp, normally UTC ISO 8601. |
| `name` | string | yes |  |
| `description` | string | yes |  |
| `goal` | string | yes |  |
| `current_direction` | string | yes |  |
| `revision` | integer | yes |  |
| `archived` | boolean | yes |  |
| `mode` | string | yes |  |
| `budget` | JsonObject | yes |  |
| `config` | JsonObject | yes |  |
| `graph_meta` | JsonObject | no | Included in list/create/update responses; omitted from the single-project GET. |

Additional properties: extensible JSON.

### `ProjectCreate`

| Field | Type | Required | Details |
| --- | --- | --- | --- |
| `name` | string | yes |  minLength=1, maxLength=240. |
| `description` | string | no |  Default: ``. |
| `goal` | string | no |  Default: ``. |
| `mode` | string or null | no |  |
| `budget` | object | no |  |
| `config` | object | no |  |

Additional properties: extensible JSON.

### `ProjectExportRequest`

| Field | Type | Required | Details |
| --- | --- | --- | --- |
| `paths` | string[] | no |  |

Additional properties: extensible JSON.

### `ProjectPatch`

| Field | Type | Required | Details |
| --- | --- | --- | --- |
| `name` | string | no |  |
| `description` | string | no |  |
| `goal` | string | no |  |
| `current_direction` | string | no |  |
| `archived` | boolean | no |  |
| `mode` | string | no |  |
| `budget` | JsonObject | no |  |
| `config` | JsonObject | no |  |
| `expected_revision` | integer | no | Optional project/graph revision; omission uses the current revision. |

Additional properties: extensible JSON.

### `ProjectProgressSnapshot`

| Field | Type | Required | Details |
| --- | --- | --- | --- |
| `project_id` | string | yes |  |
| `epoch` | string | yes |  |
| `generation` | integer | yes |  |
| `snapshot_id` | string | yes |  |
| `cursor` | integer | yes |  |
| `project_revision` | integer | yes |  |
| `observed_at` | string | yes |  |
| `controller_status` | string | yes |  |
| `facts` | ProgressFact[] | yes |  |
| `run_counts` | object | yes |  |
| `health` | ObserverHealth | yes |  |
| `dependencies` | object | yes |  |

Additional properties: rejected by the existing typed/schema-specific validator.

### `ProjectUsage`

| Field | Type | Required | Details |
| --- | --- | --- | --- |
| `project_id` | string | yes |  |
| `allow_paid` | boolean | yes |  |
| `limits` | JsonObject | yes |  |
| `estimated_cost_usd` | number or null | yes |  |
| `known_cost_usd` | number | yes |  |
| `unknown_cost_requests` | integer | yes |  |
| `reserved_usd` | number | yes |  |
| `remaining_usd` | number or null | yes |  |
| `cost_source` | string | yes |  |
| `uncertain_requests` | integer | yes |  |
| `run_count` | integer | yes |  |
| `elapsed_seconds` | number | yes |  |
| `providers` | object[] | yes |  |
| `active_run_limits` | object[] | yes |  |

Additional properties: extensible JSON.

### `ProposalApplied`

| Field | Type | Required | Details |
| --- | --- | --- | --- |
| `graph` | GraphState | yes |  |
| `accepted_indices` | integer[] | yes |  |

Additional properties: extensible JSON.

### `ProposalApplyRequest`

| Field | Type | Required | Details |
| --- | --- | --- | --- |
| `expected_revision` | integer | no |  |
| `commands` | JsonObject[] | no |  |
| `indices` | integer[] | no |  |

Additional properties: extensible JSON.

### `ProtocolRequest`

Confirmatory phase requires test_used_for_selection:false. full_submission additionally requires the full design fields documented in API_REFERENCE.md.

| Field | Type | Required | Details |
| --- | --- | --- | --- |
| `research_question` | string | yes |  |
| `hypothesis` | string | yes |  |
| `baseline` | string | yes |  |
| `candidate` | string | yes |  |
| `metric` | string | yes |  |
| `statistical_unit` | string | yes |  |
| `split_policy` | string | yes |  |
| `selection_policy` | string | yes |  |
| `decision_rule` | string | yes |  |
| `argumentative_duty` | string | yes |  Values: `effectiveness`, `mechanism`, `scenario_value`, `alternative_explanation`. |
| `meaningful_effect` | number | yes |  minimum=0. |
| `phase` | string | yes |  Values: `exploratory`, `confirmatory`. |
| `test_used_for_selection` | boolean | no |  |

Additional properties: extensible JSON.

### `ProtocolResult`

| Field | Type | Required | Details |
| --- | --- | --- | --- |
| `protocol` | JsonObject | yes |  |
| `status` | string | yes |  |
| `publication_profile` | PublicationProfile | yes |  |
| `verification_scope` | string | yes |  |

Additional properties: extensible JSON.

### `Provider`

Configured text/image endpoint. credential_ref is replaced with has_key; the stored credential itself is not returned.

| Field | Type | Required | Details |
| --- | --- | --- | --- |
| `id` | string | yes | Record identifier. |
| `created_at` | string | no | Creation timestamp, normally UTC ISO 8601. |
| `updated_at` | string | no | Last database update timestamp, normally UTC ISO 8601. |
| `name` | string | yes |  |
| `kind` | string | yes |  |
| `base_url` | string | yes |  |
| `model` | string | yes |  |
| `allow_paid` | boolean | yes |  |
| `status` | string | yes |  |
| `config` | JsonObject | yes |  |
| `has_key` | boolean | yes |  |

Additional properties: extensible JSON.

### `ProviderCreate`

| Field | Type | Required | Details |
| --- | --- | --- | --- |
| `name` | string | yes |  |
| `kind` | string | no |  Default: `openai`. Values: `ollama`, `openai`, `codex_cli`. |
| `base_url` | string | yes | HTTP(S) base URL. |
| `model` | string | yes |  |
| `allow_paid` | boolean | no |  |
| `config` | JsonObject | no |  |
| `api_key` | string | no | Optional write-only credential. |

Additional properties: extensible JSON.

### `ProviderHealth`

| Field | Type | Required | Details |
| --- | --- | --- | --- |
| `id` | string | yes |  |
| `available` | boolean | yes |  |
| `checked_at` | string | yes |  |
| `last_chat_test` | string | yes |  |
| `reason` | string | no |  |

Additional properties: extensible JSON.

### `ProviderUsage`

| Field | Type | Required | Details |
| --- | --- | --- | --- |
| `provider_id` | string | yes |  |
| `limit_usd` | number or null | yes |  |
| `estimated_cost_usd` | number or null | yes |  |
| `known_cost_usd` | number | yes |  |
| `unknown_cost_requests` | integer | yes |  |
| `reserved_usd` | number | yes |  |
| `remaining_usd` | number or null | yes |  |
| `cost_source` | string | yes |  |
| `request_count` | integer | yes |  |
| `uncertain_requests` | integer | yes |  |
| `requests` | object[] | yes |  |

Additional properties: extensible JSON.

### `ProviderWrite`

| Field | Type | Required | Details |
| --- | --- | --- | --- |
| `name` | string | no |  |
| `kind` | string | no |  |
| `base_url` | string | no |  |
| `model` | string | no |  |
| `allow_paid` | boolean | no |  |
| `config` | JsonObject | no |  |
| `api_key` | string | no | Write-only credential; nonempty values replace the stored key. |

Additional properties: extensible JSON.

### `PublicationAudit`

Delivery assessment with profile-dependent coverage and gap details. Missing manifest returns counts:{}; operational scope does not return a submission matrix. Additional assessment fields are extensible; readiness is not venue acceptance.

| Field | Type | Required | Details |
| --- | --- | --- | --- |
| `profile` | PublicationProfile | yes |  |
| `ready` | boolean | yes |  |
| `status` | string | yes |  |
| `gaps` | JsonObject[] | yes |  |
| `manifest_path` | string or null | no |  |
| `independent_verification` | JsonObject[] | no |  |
| `counts` | JsonObject | no |  |
| `targets` | JsonObject | no |  |
| `comparable_scale_counts` | JsonObject | no |  |
| `remaining_cells` | JsonObject[] | no |  |
| `verification_scope` | string | no |  |

Additional properties: extensible JSON.

### `PublicationProfile`

full_submission adds editable evidence/design targets; operational has only id, version and target.

| Field | Type | Required | Details |
| --- | --- | --- | --- |
| `id` | string | yes |  |
| `version` | integer | yes |  |
| `target` | string | yes |  |
| `manuscript_type` | string | no |  |
| `accepted_papers` | integer | no |  |
| `datasets` | integer | no |  |
| `baselines` | integer | no |  |
| `seeds` | integer | no |  |
| `ablations` | integer | no |  |
| `experiment_duties` | string[] | no |  |
| `evidence_manifest` | string | no |  |
| `scale_policy` | string | no |  |
| `budget_policy` | string | no |  |
| `writing_policy` | string | no |  |
| `architecture_figure_policy` | string | no |  |

Additional properties: extensible JSON.

### `PublicationProfilePatch`

Partial profile fields merged with the current project profile before normalization. No fields are required just to patch an existing full_submission profile.

| Field | Type | Required | Details |
| --- | --- | --- | --- |
| `id` | string | no |  |
| `version` | integer | no |  |
| `target` | string | no |  |
| `manuscript_type` | string | no |  |
| `accepted_papers` | integer | no |  |
| `datasets` | integer | no |  |
| `baselines` | integer | no |  |
| `seeds` | integer | no |  |
| `ablations` | integer | no |  |
| `experiment_duties` | string[] | no |  |
| `evidence_manifest` | string | no |  |
| `scale_policy` | string | no |  |
| `budget_policy` | string | no |  |
| `writing_policy` | string | no |  |
| `architecture_figure_policy` | string | no |  |

Additional properties: extensible JSON.

### `QueuedTaskRequest`

Task-specific fields are passed to the scheduler/worker. Scientific input schemas depend on the task kind; this is not a closed universal tool schema.

| Field | Type | Required | Details |
| --- | --- | --- | --- |
| `project_id` | string | yes |  |
| `request_id` | string or null | no |  |
| `node_id` | string or null | no |  |
| `run_ids` | string[] | no |  |
| `provider_id` | string or null | no |  |

Additional properties: extensible JSON.

### `RecordItem`

| Field | Type | Required | Details |
| --- | --- | --- | --- |
| `id` | string | yes | Record identifier. |
| `created_at` | string | yes | Creation timestamp, normally UTC ISO 8601. |
| `updated_at` | string | yes | Last database update timestamp, normally UTC ISO 8601. |
| `project_id` | string | yes |  |
| `title` | string | yes |  |
| `revision` | integer | yes |  |
| `status` | string | yes |  |
| `data` | JsonObject | yes | Editable kind-specific scientific record. Keys depend on the resource kind and task output. |

Additional properties: extensible JSON.

### `RejectionRequest`

| Field | Type | Required | Details |
| --- | --- | --- | --- |
| `expected_revision` | integer | yes |  minimum=0.0. |
| `reason` | string | yes |  minLength=1, maxLength=100000. |

Additional properties: rejected by the existing typed/schema-specific validator.

### `ReportPage`

| Field | Type | Required | Details |
| --- | --- | --- | --- |
| `items` | ReporterJob[] | yes |  |
| `next_cursor` | string or null | yes |  |

Additional properties: rejected by the existing typed/schema-specific validator.

### `ReporterJob`

| Field | Type | Required | Details |
| --- | --- | --- | --- |
| `id` | string | yes |  |
| `status` | string | yes |  |
| `created_at` | string | yes |  |
| `updated_at` | string | yes |  |
| `error` | string or null | yes |  |
| `report` | ReporterReport or null | yes |  |
| `facts` | ProgressFact[] | yes |  |
| `current` | boolean | yes |  |
| `snapshot_id` | string | yes |  |

Additional properties: rejected by the existing typed/schema-specific validator.

### `ReporterRefresh`

| Field | Type | Required | Details |
| --- | --- | --- | --- |
| `request_id` | string | yes |  minLength=1, maxLength=160. |

Additional properties: rejected by the existing typed/schema-specific validator.

### `ReporterReport`

| Field | Type | Required | Details |
| --- | --- | --- | --- |
| `snapshot_id` | string | yes |  |
| `focus` | string | yes |  Values: `activity`, `attention`, `evidence`, `no_material_change`. |
| `fact_ids` | string[] | yes |  |

Additional properties: rejected by the existing typed/schema-specific validator.

### `ReporterSettings`

| Field | Type | Required | Details |
| --- | --- | --- | --- |
| `enabled` | boolean | no |  Default: `False`. |
| `automatic` | boolean | no |  Default: `False`. |
| `provider_id` | string or null | no |  |
| `cap_usd` | number | no |  Default: `0`. minimum=0.0, maximum=10000.0. |
| `max_requests` | integer | no |  Default: `20`. minimum=1.0, maximum=1000.0. |
| `max_output_tokens` | integer or null | no |  |
| `language` | string | no |  Default: `en`. Values: `en`, `zh`. |
| `minimum_seconds` | integer | no |  Default: `60`. minimum=30.0, maximum=3600.0. |
| `maximum_wait_seconds` | integer | no |  Default: `180`. minimum=60.0, maximum=3600.0. |

Additional properties: rejected by the existing typed/schema-specific validator.

### `ReporterSettingsView`

| Field | Type | Required | Details |
| --- | --- | --- | --- |
| `settings` | ReporterSettings | yes |  |
| `version` | integer | yes |  |
| `estimated_usd` | number | yes |  |
| `reserved_usd` | number | yes |  |
| `requests` | integer | yes |  |
| `unpriced_requests` | integer | yes |  |

Additional properties: rejected by the existing typed/schema-specific validator.

### `ReporterSettingsWrite`

| Field | Type | Required | Details |
| --- | --- | --- | --- |
| `settings` | ReporterSettings | yes |  |
| `expected_version` | integer | yes |  minimum=0.0. |

Additional properties: rejected by the existing typed/schema-specific validator.

### `RepositoryCloneRequest`

| Field | Type | Required | Details |
| --- | --- | --- | --- |
| `repository` | RepositoryInput | yes |  |
| `request_id` | string | no | Optional 1–128 character retry key: [A-Za-z0-9][A-Za-z0-9_.:-]{0,127}. |

Additional properties: rejected by the existing typed/schema-specific validator.

### `RepositoryInput`

Only these five repository fields are accepted. SSH preparation needs a configured named identity/known_hosts profile on the worker.

| Field | Type | Required | Details |
| --- | --- | --- | --- |
| `url` | string | yes | Uncredentialed github.com HTTPS or git SSH repository URL; <=512 characters. |
| `ref` | string | no | Branch/tag/HEAD/commit; default HEAD; <=256 characters, validated as a safe Git ref. |
| `directory` | string | no | Relative run-workspace directory without traversal; default source; <=256 characters. |
| `transport` | string | no |  Default: `auto`. Values: `auto`, `https`, `ssh`. |
| `credential` | string or null | no | Named worker-owned credential profile, not credential material; <=64 safe name characters. |

Additional properties: rejected by the existing typed/schema-specific validator.

### `RepositoryProvenance`

| Field | Type | Required | Details |
| --- | --- | --- | --- |
| `url` | string | yes |  |
| `requested_ref` | string | yes |  |
| `commit` | string | yes |  |
| `directory` | string | yes |  |
| `transport` | string | yes |  |

Additional properties: extensible JSON.

### `ResearchAnalysisContext`

| Field | Type | Required | Details |
| --- | --- | --- | --- |
| `project` | JsonObject | yes |  |
| `graph` | GraphState | yes |  |
| `runs` | JsonObject[] | yes |  |
| `resources` | JsonObject | yes |  |
| `materials` | JsonObject[] | yes |  |
| `route_health` | RouteHealth | yes |  |
| `coverage` | JsonObject | yes |  |
| `focus` | ResearchAnalysisFocus | yes |  |
| `snapshot` | ResearchAnalysisSnapshot | yes |  |
| `provider` | JsonObject | yes |  |
| `counts` | JsonObject | yes |  |
| `execution_dependencies` | JsonObject[] | no |  |
| `tool_executions` | JsonObject[] | no |  |
| `events` | JsonObject[] | no |  |
| `file_revisions` | JsonObject[] | no |  |

Additional properties: extensible JSON.

### `ResearchAnalysisFocus`

| Field | Type | Required | Details |
| --- | --- | --- | --- |
| `scope` | string | yes |  |
| `node_id` | string or null | yes |  |
| `branch_id` | string or null | yes |  |
| `node_ids` | string[] | yes |  |
| `run_ids` | string[] | yes |  |
| `upstream_node_ids` | string[] | yes |  |
| `downstream_node_ids` | string[] | yes |  |

Additional properties: extensible JSON.

### `ResearchAnalysisHistory`

| Field | Type | Required | Details |
| --- | --- | --- | --- |
| `runs` | ResearchAnalysisHistoryItem[] | yes |  |

Additional properties: extensible JSON.

### `ResearchAnalysisHistoryItem`

| Field | Type | Required | Details |
| --- | --- | --- | --- |
| `id` | string | yes |  |
| `status` | string | yes |  |
| `created_at` | string | yes |  |
| `error` | string or null | yes |  |
| `scope` | string | yes |  |
| `node_id` | string or null | yes |  |
| `branch_id` | string or null | yes |  |
| `question` | string | yes |  |
| `project_revision` | integer | yes |  |
| `report_available` | boolean | yes |  |

Additional properties: extensible JSON.

### `ResearchAnalysisResult`

| Field | Type | Required | Details |
| --- | --- | --- | --- |
| `run_id` | string | yes |  |
| `status` | string | yes |  |
| `error` | string or null | yes |  |
| `artifact_error` | string or null | yes |  |
| `report` | JsonObject or null | yes |  |
| `citations` | JsonObject[] | yes |  |
| `literature_searches` | JsonObject[] | yes |  |
| `snapshot` | ResearchAnalysisSnapshot | yes |  |
| `current` | boolean | yes |  |
| `stale_reasons` | string[] | yes |  |
| `artifacts` | JsonObject | yes |  |
| `model` | string or null | no |  |
| `usage` | JsonObject | no |  |
| `focus` | ResearchAnalysisFocus | no |  |
| `question` | string | no |  |
| `verification_scope` | string | no |  |

Additional properties: extensible JSON.

### `ResearchAnalysisSnapshot`

| Field | Type | Required | Details |
| --- | --- | --- | --- |
| `project_revision` | integer | yes |  |
| `fingerprint` | string | yes |  |
| `path` | string | no |  |
| `sha256` | string | no |  |

Additional properties: extensible JSON.

### `ResearchControl`

| Field | Type | Required | Details |
| --- | --- | --- | --- |
| `status` | string | yes |  |
| `phase` | string | no |  |
| `branch_id` | string or null | no |  |
| `required_artifacts` | JsonValue[] | no |  |
| `autonomous` | boolean | no |  |
| `legacy_max_cycles` | integer or null | no | Historical planning cap; never enforced. |
| `paused_run_ids` | string[] | no |  |
| `process_control_errors` | object[] | yes |  |

Additional properties: extensible JSON.

### `ResearchControlRequest`

| Field | Type | Required | Details |
| --- | --- | --- | --- |
| `branch_id` | string or null | no |  |
| `required_artifacts` | JsonValue[] | no |  |
| `autonomous` | boolean | no |  |
| `max_cycles` | integer or null | no | Deprecated legacy input; recorded as history and does not limit research lifetime. |

Additional properties: extensible JSON.

### `ResearchNode`

A path node. Additional kernel/runtime metadata is flattened into this object, not returned as an extra field. Newly created graph snapshots need not contain database timestamps.

| Field | Type | Required | Details |
| --- | --- | --- | --- |
| `id` | string | yes | Record identifier. |
| `created_at` | string | no | Creation timestamp, normally UTC ISO 8601. |
| `updated_at` | string | no | Last database update timestamp, normally UTC ISO 8601. |
| `project_id` | string | yes |  |
| `branch_id` | string | yes |  |
| `type` | string | yes |  |
| `title` | string | yes |  |
| `instructions` | string | yes |  |
| `revision` | integer | yes |  |
| `config` | JsonObject | yes |  |
| `position` | object | yes |  |
| `execution_status` | string | yes |  |
| `research_status` | string | yes |  |
| `deliverable_status` | string | yes |  |
| `archived` | boolean | yes |  |
| `inputs` | JsonValue[] | yes |  |
| `outputs` | JsonValue[] | yes |  |
| `context_overrides` | JsonObject | yes |  |
| `comments` | JsonValue[] | yes |  |

Additional properties: extensible JSON.

### `ResearchState`

| Field | Type | Required | Details |
| --- | --- | --- | --- |
| `objective` | JsonObject | yes |  |
| `trials` | Trial[] | yes |  |
| `controller` | JsonObject | yes |  |
| `active_runs` | object[] | yes |  |
| `decisions` | RecordItem[] | yes |  |
| `counts` | object | yes |  |
| `coverage` | string | no |  |

Additional properties: extensible JSON.

### `ResourceCreate`

| Field | Type | Required | Details |
| --- | --- | --- | --- |
| `project_id` | string | yes |  |
| `title` | string | no |  Default: ``. |
| `data` | object | no |  |

Additional properties: extensible JSON.

### `ResourcePatch`

| Field | Type | Required | Details |
| --- | --- | --- | --- |
| `title` | string | no |  |
| `status` | string | no |  |
| `data` | JsonObject | no |  |
| `replace_data` | boolean | no | When true, replace the complete resource data object instead of merging it. |
| `expected_revision` | integer | no | Optional resource revision; omission uses the current resource revision. |

Additional properties: extensible JSON.

### `ResourceRecord`

Type: `RecordItem`.

### `ReviewApplyRequest`

| Field | Type | Required | Details |
| --- | --- | --- | --- |
| `expected_revision` | integer | yes |  |
| `indices` | integer[] | yes |  |

Additional properties: extensible JSON.

### `ReviewData`

Task-specific review. A paper_generation review stores a complete proposed source/assets bundle and accepts indices:[0]; ordinary paper revisions store exact original/replacement spans. Statistical/scientific review findings remain extensible.

| Field | Type | Required | Details |
| --- | --- | --- | --- |
| `kind` | string | no |  |
| `origin` | string | no |  |
| `summary` | string | no |  |
| `paper_id` | string or null | no |  |
| `paper_revision` | integer or null | no |  |
| `run_id` | string | no |  |
| `issues` | JsonValue[] | no |  |
| `edits` | JsonObject[] | no |  |
| `queued_paper_snapshot` | JsonObject or null | no |  |
| `proposed_title` | string | no |  |
| `proposed_data` | PaperData | no |  |
| `diff` | string | no |  |

Additional properties: extensible JSON.

### `ReviewRecord`

| Field | Type | Required | Details |
| --- | --- | --- | --- |
| `id` | string | yes | Record identifier. |
| `created_at` | string | yes | Creation timestamp, normally UTC ISO 8601. |
| `updated_at` | string | yes | Last database update timestamp, normally UTC ISO 8601. |
| `project_id` | string | yes |  |
| `title` | string | yes |  |
| `revision` | integer | yes |  |
| `status` | string | yes |  |
| `data` | ReviewData | yes |  |

Additional properties: extensible JSON.

### `RouteHealth`

| Field | Type | Required | Details |
| --- | --- | --- | --- |
| `replan_required` | boolean | yes |  |
| `signals` | RouteSignal[] | yes |  |
| `settings` | RouteReviewSettings | yes |  |
| `reviewed_run_ids` | string[] | yes |  |
| `node_count` | integer | yes |  |
| `total_run_count` | integer | yes |  |
| `scope` | string | yes |  |

Additional properties: extensible JSON.

### `RouteReviewSettings`

| Field | Type | Required | Details |
| --- | --- | --- | --- |
| `enabled` | boolean | yes |  |
| `window` | integer | yes |  |
| `repeated_failures` | integer | yes |  |
| `unchanged_counterexample_repeats` | integer | yes |  |
| `planning_without_execution` | integer | yes |  |
| `review_every_runs` | integer | yes |  |

Additional properties: extensible JSON.

### `RouteSignal`

| Field | Type | Required | Details |
| --- | --- | --- | --- |
| `kind` | string | yes |  |
| `node_ids` | string[] | yes |  |
| `run_ids` | string[] | yes |  |
| `finding` | string | yes |  |

Additional properties: extensible JSON.

### `Run`

A queued or saved task run. A successful submission means it was enqueued, not that its scientific work completed. Configuration redaction differs by view; see the endpoint description.

| Field | Type | Required | Details |
| --- | --- | --- | --- |
| `id` | string | yes | Record identifier. |
| `created_at` | string | no | Creation timestamp, normally UTC ISO 8601. |
| `updated_at` | string | no | Last database update timestamp, normally UTC ISO 8601. |
| `project_id` | string | yes |  |
| `node_id` | string or null | yes |  |
| `branch_id` | string or null | yes |  |
| `request_id` | string | yes |  |
| `kind` | string | yes |  |
| `status` | string | yes |  |
| `config` | JsonObject | yes |  |
| `node_revision` | integer | yes |  |
| `priority` | integer | yes |  |
| `dependencies` | string[] | yes |  |
| `worker_id` | string or null | yes |  |
| `pid` | integer or null | yes |  |
| `process_created` | number or null | yes | Process identity creation time, not an ISO timestamp. |
| `started_at` | string or null | yes |  |
| `finished_at` | string or null | yes |  |
| `exit_code` | integer or null | yes |  |
| `error` | string or null | yes |  |
| `output_path` | string | yes | Path relative to the project workspace. |
| `metrics` | JsonObject | yes |  |
| `resource` | JsonObject | yes |  |
| `manuscript_evidence` | object | no |  |

Additional properties: extensible JSON.

### `RunActionRequest`

| Field | Type | Required | Details |
| --- | --- | --- | --- |
| `request_id` | string | no | Used by retry; other run actions ignore it. |
| `priority` | integer | no | Used by priority, default 0; handler converts with int(). |
| `context_char_budget` | integer | no | resume only; Agent run, integer >= 1000. |
| `agent_budget` | JsonObject | no | resume only; merges editable per-Agent token/cost/time hard limits after budget validation. Legacy steps is accepted as a bounded work-round hint, never a cumulative research limit. |

Additional properties: extensible JSON.

### `RunCollection`

| Field | Type | Required | Details |
| --- | --- | --- | --- |
| `runs` | Run[] | yes |  |
| `run_ids` | string[] | no |  |

Additional properties: extensible JSON.

### `RunConfiguration`

| Field | Type | Required | Details |
| --- | --- | --- | --- |
| `run_id` | string | yes |  |
| `config` | JsonObject | yes |  |

Additional properties: extensible JSON.

### `RunDetail`

Type: `Run & object`.

### `RunDiagnostics`

| Field | Type | Required | Details |
| --- | --- | --- | --- |
| `run_id` | string | yes |  |
| `status` | string | yes |  |
| `phase` | string | yes |  |
| `execution_backend` | string | yes |  |
| `repository` | JsonObject or null | yes |  |
| `processes` | ProcessDiagnostic[] | yes |  |
| `processes_truncated` | boolean | yes |  |
| `logs` | object | yes |  |
| `elapsed_seconds` | number or null | yes |  |
| `observed_at` | number | yes |  |
| `observation` | string | yes |  |
| `remote_state_is_live` | boolean | yes |  |

Additional properties: extensible JSON.

### `RunLineage`

| Field | Type | Required | Details |
| --- | --- | --- | --- |
| `run_id` | string | yes |  |
| `project_id` | string | yes |  |
| `runs` | LineageRun[] | yes |  |

Additional properties: extensible JSON.

### `RunRepository`

| Field | Type | Required | Details |
| --- | --- | --- | --- |
| `run_id` | string | yes |  |
| `project_id` | string | yes |  |
| `status` | string | yes |  |
| `repository` | RepositoryProvenance or null | yes |  |

Additional properties: extensible JSON.

### `RunRequest`

| Field | Type | Required | Details |
| --- | --- | --- | --- |
| `request_id` | string or null | no |  |
| `scope` | string | no |  Default: `single`. Values: `single`, `ancestors`, `descendants`, `affected`, `to_here`, `from_here`, `subtree`. |
| `config` | object | no |  |

Additional properties: extensible JSON.

### `RunSession`

| Field | Type | Required | Details |
| --- | --- | --- | --- |
| `run_id` | string | yes |  |
| `status` | string | yes |  |
| `session` | JsonObject or null | yes |  |

Additional properties: extensible JSON.

### `ScopeCoverage`

| Field | Type | Required | Details |
| --- | --- | --- | --- |
| `id` | string | yes |  |
| `kind` | string | yes |  |
| `object_id` | string | yes |  |
| `attempt_id` | string or null | yes |  |
| `scan_generation` | integer | yes |  |
| `coverage` | string | yes |  |
| `observed_at` | string or null | yes |  |
| `error` | string or null | yes |  |
| `total` | integer | yes |  |

Additional properties: rejected by the existing typed/schema-specific validator.

### `ScopePage`

| Field | Type | Required | Details |
| --- | --- | --- | --- |
| `items` | ScopeCoverage[] | yes |  |
| `next_cursor` | string or null | yes |  |

Additional properties: rejected by the existing typed/schema-specific validator.

### `SelectedRunCollection`

| Field | Type | Required | Details |
| --- | --- | --- | --- |
| `runs` | Run[] | yes |  |
| `run_ids` | string[] | yes |  |

Additional properties: extensible JSON.

### `SelectedRunsRequest`

| Field | Type | Required | Details |
| --- | --- | --- | --- |
| `node_ids` | string[] | no |  |
| `request_id` | string or null | no |  |
| `config` | JsonObject | no |  |

Additional properties: extensible JSON.

### `ShareCreated`

| Field | Type | Required | Details |
| --- | --- | --- | --- |
| `id` | string | yes |  |
| `url` | string | yes |  |
| `token` | string | yes |  |

Additional properties: extensible JSON.

### `ShareRequest`

| Field | Type | Required | Details |
| --- | --- | --- | --- |
| `node_ids` | string[] | no |  |
| `description` | boolean | no |  |

Additional properties: extensible JSON.

### `ShareSnapshot`

| Field | Type | Required | Details |
| --- | --- | --- | --- |
| `project` | object | yes |  |
| `nodes` | object[] | yes |  |
| `read_only` | boolean | yes |  |

Additional properties: extensible JSON.

### `SourceRef`

| Field | Type | Required | Details |
| --- | --- | --- | --- |
| `project_id` | string | yes |  |
| `epoch` | string or null | no |  |
| `kind` | string | yes |  Values: `project`, `node`, `run`, `paper`, `figure`, `file`. |
| `object_id` | string | yes |  |
| `revision` | integer or null | no |  |
| `scope_id` | string or null | no |  |
| `path` | string or null | no |  |
| `file_generation` | integer or null | no |  |
| `segment_id` | string or null | no |  |
| `attempt_id` | string or null | no |  |
| `record_updated_at` | string or null | no |  |

Additional properties: rejected by the existing typed/schema-specific validator.

### `SourceSegment`

| Field | Type | Required | Details |
| --- | --- | --- | --- |
| `id` | string | yes |  |
| `name` | string | yes |  |
| `kind` | string | yes |  |
| `start_byte` | integer | yes |  |
| `end_byte` | integer | yes |  |
| `start_line` | integer | yes |  |
| `end_line` | integer | yes |  |
| `parser` | string | yes |  |
| `certainty` | string | no |  Default: `structural`. Values: `structural`, `raw`. |

Additional properties: rejected by the existing typed/schema-specific validator.

### `SourceView`

| Field | Type | Required | Details |
| --- | --- | --- | --- |
| `source` | SourceRef | yes |  |
| `availability` | string | yes |  Values: `available`, `changed`, `deleted`, `unavailable`. |
| `content` | string or null | no |  |
| `segments` | SourceSegment[] | no |  |
| `parse_state` | string | yes |  |
| `observed_at` | string or null | no |  |
| `note` | string | yes |  |
| `project_path` | string or null | no |  |
| `metadata` | object | no |  |
| `artifact_url` | string or null | no |  |

Additional properties: rejected by the existing typed/schema-specific validator.

### `SymbolicCheckRequest`

| Field | Type | Required | Details |
| --- | --- | --- | --- |
| `project_id` | string | yes |  |
| `expression` | string | yes |  |
| `variable` | string | no |  |
| `kind` | string | no |  |
| `values` | JsonObject | no |  |
| `title` | string | no |  |

Additional properties: extensible JSON.

### `SystemState`

| Field | Type | Required | Details |
| --- | --- | --- | --- |
| `cpu_percent` | number | yes |  |
| `memory` | object | yes |  |
| `disk` | object | yes |  |
| `gpu` | object[] | yes |  |
| `workers` | Worker[] | yes |  |
| `latex` | string or null | yes |  |
| `model_connected` | boolean | yes |  |
| `provider_status` | ProviderHealth[] | yes |  |
| `platform` | string | yes |  |
| `isolation` | string | yes |  |

Additional properties: extensible JSON.

### `TablePreview`

| Field | Type | Required | Details |
| --- | --- | --- | --- |
| `path` | string | yes |  |
| `format` | string | yes |  |
| `columns` | string[] | yes |  |
| `rows` | JsonObject[] | yes |  |
| `total` | integer | yes |  |
| `offset` | integer | yes |  |
| `limit` | integer | yes |  |
| `returned` | integer | yes |  |
| `has_more` | boolean | yes |  |
| `truncated_columns` | boolean | yes |  |
| `column_count` | integer | yes |  |
| `truncated_cells` | integer | yes |  |

Additional properties: extensible JSON.

### `TaskRun`

Type: `Run`.

### `ToolExecution`

| Field | Type | Required | Details |
| --- | --- | --- | --- |
| `id` | string | yes | Record identifier. |
| `created_at` | string | no | Creation timestamp, normally UTC ISO 8601. |
| `updated_at` | string | no | Last database update timestamp, normally UTC ISO 8601. |
| `run_id` | string | yes |  |
| `tool` | string | yes |  |
| `arguments` | JsonObject | yes |  |
| `status` | string | yes |  |
| `result` | JsonObject | yes |  |
| `elapsed` | number or null | yes |  |

Additional properties: extensible JSON.

### `Trial`

| Field | Type | Required | Details |
| --- | --- | --- | --- |
| `run_id` | string | yes |  |
| `status` | string | yes |  |
| `metric` | string | yes |  |
| `value` | number or null | yes |  |
| `disposition` | string | yes |  |
| `conditions` | JsonObject | no |  |
| `comparison_declaration` | ComparisonDeclaration | no |  |
| `comparison_eligible` | boolean | no |  |
| `verification_status` | string | no |  |
| `numerical_verification` | JsonObject | no |  |
| `evidence_label` | string | no |  |
| `comparison_status` | string | no |  |
| `comparator_run_id` | string or null | no |  |
| `exploratory_group` | JsonObject | no |  |

Additional properties: extensible JSON.

### `ValidationError`

| Field | Type | Required | Details |
| --- | --- | --- | --- |
| `loc` | string or integer[] | yes |  |
| `msg` | string | yes |  |
| `type` | string | yes |  |
| `input` | any JSON | no |  |
| `ctx` | object | no |  |

Additional properties: extensible JSON.

### `Verification`

Verifier runs return their current verdict; producers return current linked verifier verdicts. Extra binding/check data depends on the declared verification contract. Completion alone is not acceptance.

| Field | Type | Required | Details |
| --- | --- | --- | --- |
| `run_id` | string | yes |  |
| `verification_status` | string | yes |  |
| `reason` | string | no |  |
| `verification_run_id` | string | no |  |
| `verifier_node_id` | string or null | no |  |
| `checks` | JsonObject[] | yes |  |
| `check_scope_paths` | string[] | yes |  |
| `numerical_scope` | JsonObject[] | no |  |
| `verification_scope` | string | yes |  |
| `verifications` | JsonObject[] | no |  |

Additional properties: extensible JSON.

### `VerificationRequest`

Other task configuration keys pass through. Service-owned verification bindings, source identities, verdicts and receipts cannot be supplied by the caller.

| Field | Type | Required | Details |
| --- | --- | --- | --- |
| `project_id` | string | yes |  |
| `node_id` | string or null | no |  |
| `request_id` | string or null | no |  |
| `verification` | JsonObject | no |  |
| `command` | JsonValue | no |  |
| `execution_backend` | string | no |  |

Additional properties: extensible JSON.

### `VisualSelection`

Selected actual candidate, complete ranking and independent review results. Optional raster completion adds publication_gate_passed/quality_status. placement can be null.

| Field | Type | Required | Details |
| --- | --- | --- | --- |
| `version` | integer | no |  |
| `status` | string | no |  |
| `candidate_id` | string | no |  |
| `ranking` | object[] | no |  |
| `reviews` | JsonObject[] | no |  |
| `placement` | JsonObject or null | no |  |
| `selection_policy` | string | no |  |
| `publication_gate_passed` | boolean | no |  |
| `quality_status` | string | no |  |

Additional properties: extensible JSON.

### `Worker`

| Field | Type | Required | Details |
| --- | --- | --- | --- |
| `id` | string | yes | Record identifier. |
| `created_at` | string | no | Creation timestamp, normally UTC ISO 8601. |
| `updated_at` | string | no | Last database update timestamp, normally UTC ISO 8601. |
| `name` | string | yes |  |
| `pid` | integer | yes |  |
| `heartbeat` | string | yes |  |
| `capabilities` | JsonObject | yes |  |
| `online` | boolean | yes |  |

Additional properties: extensible JSON.

### `WritingEdit`

| Field | Type | Required | Details |
| --- | --- | --- | --- |
| `original` | string | yes |  |
| `replacement` | string or null | yes |  |
| `reason` | string | yes |  |
| `start` | integer | yes |  |
| `end` | integer | yes |  |
| `line` | integer | yes |  |
| `requires_evidence_judgment` | boolean | yes |  |

Additional properties: extensible JSON.

### `WritingReview`

| Field | Type | Required | Details |
| --- | --- | --- | --- |
| `edits` | WritingEdit[] | yes |  |
| `style_version` | integer | yes |  |
| `profile` | JsonObject | yes |  |
| `coverage` | string | yes |  |
| `editing_policy` | string | yes |  |

Additional properties: extensible JSON.

### `WritingReviewRequest`

| Field | Type | Required | Details |
| --- | --- | --- | --- |
| `source` | string | no |  |

Additional properties: extensible JSON.
