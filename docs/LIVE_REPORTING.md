# Live progress and source inspection

Start FOREST using the [local or Compose installation](DEPLOYMENT.md). The normal
API starts the observer; the normal worker continues to own research execution.
There is no separate reporting daemon to initialize. Open a project's **Overview**
for recorded activity, attention, blocked work, recent changes, queued work and
last-checked evidence. Open **Files → Source explorer** for scoped observations.

## Reading progress

Progress works without a model. Counts include persisted runs; detail is bounded
to 100 active and 20 recent runs. A running database row and a recent heartbeat
do not prove a live process. Historical attempts remain historical when a newer
attempt or node revision exists. The evidence panel reports the last recorded
check and its time; it does not recalculate scientific acceptance. Use **Run
evidence** or **Inspect checked comparisons** to request the existing checks.

The evidence panel also shows up to 20 saved figure records and the last saved
controller delivery audit. Figure rendering, visual review and producer
applicability remain separate. Superseded producers are historical; source bytes
and delivery readiness are not rechecked by dashboard reads. Figure links bind
the saved update time as well as revision, since a render can update outputs
without advancing the editable figure revision.

The browser shares one project event stream and reconciles every two seconds.
An event-invalidated progress read can rebuild its bounded database projection
without waiting for unrelated projects' file scans. Concurrent refreshes coalesce,
and projection construction is limited to four per second per project.
The observer normally advances once per second for a small installation. Its age,
coverage, unavailable native hints and event-history gaps are shown explicitly.
Large installations rotate projects and scopes fairly, so displayed age is the
freshness boundary. Restarting the API resumes bounded reconciliation. If hints
fail, polling continues and the watcher is retried.

## Scoped sources

Project files, branch workspaces, run outputs, run workspaces and remote
workspaces have separate identities. Choose a scope kind and optionally enter a
branch/run ID, then page its inventory. A run's proposed output is never promoted
to a branch merely by observing it. Remote observations are collected by the
owning SSH executor; opening a source does not contact that host. The saved
observation time and unknown live remote currentness remain visible.

Links bind project epoch, scope, path, observed generation, attempt and section.
An edit, deletion, new attempt or restore makes an old link changed, deleted or
unavailable. Historical file bytes are not archived. Exact retained small
artifacts use generation-checked URLs; a source link cannot silently download a
replacement at the same path. Source inspection leaves editor drafts alone and
keeps the existing conflict dialog for stale saves.

Python symbols, Markdown headings, JSON Pointers and safely parsed YAML keys
have structural locations. Duplicate headings are disambiguated. TOML keys,
LaTeX sections/environments/input locators, logs and unsupported languages have
explicit raw or partial source ranges. LaTeX inputs are locators, not macro
expansion or recursive external-file admission. JavaScript/TypeScript uses the
raw fallback. UTF-8 byte offsets are zero-based and end-exclusive; displayed
lines are one-based.

CSV/TSV exposes bounded columns and sample rows; sample counts are not dataset
counts. Parquet reads a bounded footer for actual schema, row and row-group
metadata, without reading dataset rows. Small PDFs use actual page metadata and
the existing PDF viewer; images use bounded header dimensions and an actual
preview. Checkpoints are metadata only and are never unpickled. Large PDFs,
unsupported metadata and content beyond a bound remain explicitly unavailable
for exact excerpts. The ordinary Files view can still open an owner-selected
current artifact.

Each reconciliation pass processes at most 200 entries and 2 MiB of content.
Current retained content is limited to 256 KiB per file and 32 MiB per project;
metadata is limited to 8 KiB per file. Inventories allow 100,000 file rows,
including deletion tombstones. A scope resumes its depth-first scan using a
stack of up to 2,048 saved directory cursors, so wide folders continue across
passes. Capacity or permission failures leave the scan partial.
CSV prefix reads are 32 KiB; Parquet footer and image-header reads are 256 KiB.
Remote pages allow 200 entries, 32 KiB per text file and 512 KiB total text.
Hidden paths, dependency caches, credentials, private keys and agent-session
stores are excluded. Unregistered external datasets are not admitted.

## Optional narration

Narration is disabled by default. In **Settings**, save a provider. In **Overview
→ Optional narrative settings**, choose it, enable narration and set a request
cap. For a priced external provider, also authorize paid usage in the provider
and project and set a reporting USD cap. **Refresh narrative** explicitly queues
a job. Automatic refresh is a separate opt-in for meaningful recorded changes.
Reads, timers, browser reconnects and report publication do not themselves make
model calls. Settings changes use a version check and cancel pending jobs.

The model selects and orders up to 12 service facts. FOREST renders their values,
scope and applicability verbatim. It cannot invent completion, numbers or
scientific acceptance through prose. This release does not generate open-ended
scientific interpretations or read research goals, source excerpts, logs or
agent sessions into the narrator. Its context is the compact operational
snapshot. Reporter jobs have their own tables and do not consume experiment
slots or add scientific TaskRuns.

At most one active job is owned by a project, with two active jobs and 200 queued
jobs across API instances. Jobs have 180-second leases; provider timeouts are at
most 120 seconds and ambiguous sends are not automatically replayed. Dispatch
leases survive cancellation and project deletion until an
in-flight inference returns or its lease expires. This preserves the global
concurrency bound while discarding late results. Publication
rechecks policy, lease, epoch, provider version and snapshot dependencies. A
failed or stale report leaves deterministic progress usable. Inspect shared
usage before retrying an uncertain request.

### Local Codex provider

Install Codex CLI on the API/worker host and sign in there. Create a provider with
kind `codex_cli`, base URL `http://localhost`, model `gpt-6-luna`, and configuration:

```json
{"reasoning_effort":"xhigh","timeout":120}
```

FOREST reuses the CLI's existing login; it does not copy login credentials into
the provider. The inference child uses an empty read-only working directory,
ephemeral execution and disabled shell, MCP, apps, plugins, browser and other
tools. FOREST owns any research-agent tools separately. `FOREST_CODEX_EXECUTABLE`
can select the installed executable. CLI health checks login, not model
availability; inference errors remain visible.

Codex reports actual token usage but does not supply USD pricing or a hard output
token ceiling. The request cap and timeout apply; a USD cap cannot establish a
subscription charge ceiling. These requests show unknown USD cost in the shared
ModelRequest ledger. A subscription request is not represented as a free paid
API call. A containerized API needs its own available CLI/login setup; the task
container image alone does not provide it.

## Headless use

Commands are pure API clients and require a running service:

```sh
forest --server http://127.0.0.1:8000 --project PROJECT_ID progress show --json
forest --project PROJECT_ID progress scopes --json
forest --project PROJECT_ID progress sources --scope SCOPE_ID --json
forest --project PROJECT_ID progress source --file source-reference.json --json
forest --project PROJECT_ID report settings --json
forest --project PROJECT_ID report configure --file reporter-settings.json --json
forest --project PROJECT_ID report refresh --request-id UNIQUE_ID --json
forest --project PROJECT_ID report show --json
```

A settings file contains `expected_version` and the full `settings` object from
readback. A source-reference file contains the returned `source` object. Use a
reused request ID for retries of the same refresh. Duplicate refreshes coalesce;
an unchanged published or uncertain snapshot is reused.
Coalesced request IDs retain their original job through later state changes.
Deduplication retains up to 1,000 request IDs per project and expires with the
retired report job; reuse a fresh ID only when deliberately requesting new work.

## Privacy, upgrade and restore

Progress, sources, artifacts, settings and reports require the owner boundary.
They are not included in public shares. Forwarded HTTP requests require owner
authentication even when a proxy preserves a localhost Host. Configure a proxy
to preserve forwarded client identity and pass owner authentication; do not
strip that identity to obtain loopback access. Origin checks still apply.

Startup adds schema version 3 reporting tables without changing scientific
records. Repeated startup is idempotent. Stop API and worker before using the
existing [backup/restore commands](DEPLOYMENT.md). Restore clears projections,
source excerpts and jobs, disables automatic billing and rebuilds a new epoch.
Re-enable narration only after reviewing provider availability and usage.
Project deletion cascades reporting records and clears derived content. In-flight
reserved usage remains uncertain rather than being refunded by deleting a job.

## Reproducing checks

```sh
python scripts/export_api_contract.py --check
python -m pytest -q tests/test_observation.py
FOREST_TEST_POSTGRES=postgresql://TEST_ROLE@127.0.0.1:5432/postgres \
  python -m pytest -q tests/test_observation.py tests/test_postgres.py
npm --prefix apps/web run build
forest serve --port 18340 --data-dir /absolute/private/test-data
FOREST_WEB_URL=http://127.0.0.1:18340 npm --prefix apps/web run test:e2e -- progress.spec.ts
```

PostgreSQL tests create and drop fresh `forest_test_*` databases; use a dedicated
test role authorized for that operation. Browser tests use the real production
build, API and worker. Unit-injected provider responses exercise rejection and
lease races and are not live-provider qualification. Docker/SSH and real model
qualification require their actual engines, host configuration and account.
