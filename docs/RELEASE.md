# Release qualification

FOREST is a release candidate for a trusted single-owner workstation. This guide defines reproducible checks and reporting boundaries. It does not declare a release production-ready because a command exists or an Agent says it succeeded.

## Independent dimensions

| Check | Command | What a pass establishes |
| --- | --- | --- |
| Local regression | `./scripts/test.sh` | Actual selected backend checks, frontend compilation and component checks on this environment |
| Agent capability | `qualify_release.py agents` | Outputs and execution receipts satisfy the external oracle for the selected bounded tasks |
| Graph scale | `qualify_release.py graph --nodes 1000` | Actual API storage, pagination, edge preservation and an ordinary edit on a 1,000-node graph |
| Compute endurance | `qualify_release.py compute --seconds 14400` | Actual training reports at least four hours and saved predictions independently reproduce the metrics |
| Service soak | `qualify_release.py soak --seconds 86400` | Successful sampled API writes, reads and real worker computations over 24 observed monitoring hours |

Graph scale does not measure 1,000 concurrent computations or browser frame rate. Compute endurance and service soak are different checks. Short runs validate the harness without satisfying longer duration targets. A stopped monitor can be resumed; gaps between soak monitoring segments are excluded from observed duration.

## Local tests and CI

```sh
./scripts/test.sh
.venv/bin/python -m pytest tests/test_release_qualification.py -q
```

Default CI uses isolated SQLite tests and frontend build/tests. Live-model tests require explicit environment opt-in. PostgreSQL qualification requires an actual test server and `FOREST_TEST_POSTGRES=1`; it creates a separate randomized database. Do not point cleanup or test tools at a working database.

The release harness itself has local tests for corpus/reference consistency, real saved digit-label provenance, invalid/missing result rejection, source-archive exclusions, actual graph API edits, actual worker training, and all task instructions fitting the runtime context builder. It supplies inputs and output oracles; it does not supply a fake model, prerecorded successful tool response, or task implementation.

## Thirty real Agent tasks

```sh
.venv/bin/python scripts/qualify_release.py list
.venv/bin/python scripts/qualify_release.py agents \
  --provider-id YOUR_CONFIGURED_PROVIDER_ID \
  --report var/qa/release-agents.json
```

Tasks run sequentially. The first starts with an empty graph and the original autonomous prime-count goal: the real planner must create an executable route, and the Agent must implement and execute independent trial-division and sieve algorithms. The oracle recomputes the exact count, checks saved source structure for distinct mechanisms, and requires actual process receipts. Full source artifacts remain available for manual algorithm review; structural checks alone are not a proof of independence.

The remaining tasks cover exact arithmetic, algorithms, table analysis, file parsing and evidence-bound writing. Small constructed arrays/texts are labeled software inputs, not scientific observations. Writing tasks recompute actual saved predictions from public handwritten-digits qualification runs, preserve observation identifiers, cite provenance, and identify seed, regularization and compute-budget confounds. They do not present artificial performance tables as experimental results. Source fixtures and provenance are in `scripts/qualification/fixtures/`.

Corpus version 2 added explicit result-key names. Version 3 added top-level JSON types, made the text-parsing count request explicit, and removed the default per-task step cap. Version 5 requires writing reports to label the baseline mean, candidate mean, and signed improvement beside their values, such as `baseline mean: 0.913`. Oracle revision 3 checks those associations. In-flight attempts use their saved instructions and oracle revision. The current executor uses `--round-steps` as an optional work-round size hint with automatic checkpoint continuation; the legacy `--max-steps` spelling is accepted with the same meaning. The shared provider spend cap and specified task time budget still apply. Resource usage is read from the separate supervisory ledger. Instructions and output contracts are saved per new attempt, without revealing expected values. Earlier failures and their original conditions remain visible, including genuine wrong numerical results; they must not all be reclassified as formatting or harness failures. Cross-version retries are reported separately from first-attempt outcomes.

Each computational task must write and execute its own code. An external oracle checks result files and process receipts. Writing checks cover required numerical/source bindings and stated scope, not publication quality, unrestricted web research, or scientific originality. The model's own success statement is not the score.

For paid providers, first configure `budget_usd` and USD pricing on the provider. The API owns credentials and atomic request reservations. Then explicitly opt in:

```sh
.venv/bin/python scripts/qualify_release.py agents \
  --provider-id YOUR_CONFIGURED_PROVIDER_ID --allow-paid --max-cost-usd 5 \
  --report var/qa/release-agents.json
```

The runner requires the provider's shared cap to be no higher than this limit and reads `/api/providers/{id}/usage`; it does not create a second independent spending allowance. The cap applies to other callers using that provider too. Unknown usage is not silently treated as free. Review provider billing separately from local estimated usage.

Resume without rerunning finished tasks:

```sh
.venv/bin/python scripts/qualify_release.py agents --resume \
  --provider-id YOUR_CONFIGURED_PROVIDER_ID --allow-paid --max-cost-usd 5 \
  --report var/qa/release-agents.json
```

Use repeated `--task TASK_ID` options for a subset. `--retry-failed --resume` records another attempt while retaining prior failures. Reports include selected task counts; a subset pass must not be reported as all 30 passing. Changing providers, prompts or budgets changes the comparison conditions and must be disclosed.

### Read-only reevaluation of an existing run

Oracle revision 2 treats equal JSON integer/float values such as `10` and `10.0` as the same mathematical number; booleans remain distinct. Integer-valued references require exact numeric equality, while existing floating-point reference tolerances are unchanged. Actual wrong numerical results are still failures. This is an evaluator correction, not a changed Agent output or a new successful execution.

```sh
.venv/bin/python scripts/qualify_release.py rescore \
  --report var/qa/release-agents.json --task 16-missing-data
```

Reevaluation reads artifacts and process receipts from completed runs with a recorded grade through the API and makes no model call. Resume an ungraded attempt to record its original evaluation before rescoring it. Reevaluation preserves the original `status`, `failures`, conditions and summary, appends `evaluation_history`, and records the corrected verdict under `latest_evaluation` plus `rescored_summary`. Later retries use that explicit latest evaluation to avoid paying to rerun an already-correct result. A report cannot be rescored while another runner owns its monitor lock.

## Graph, four-hour compute, and 24-hour soak

Start the ordinary API and worker first. These checks do not need paid model calls.

```sh
.venv/bin/python scripts/qualify_release.py graph --nodes 1000
.venv/bin/python scripts/qualify_release.py compute --seconds 14400 --submit-only
.venv/bin/python scripts/qualify_release.py compute --seconds 14400 --resume
.venv/bin/python scripts/qualify_release.py soak --seconds 86400
```

The compute job runs real softmax training on the bundled digits dataset. Its requested duration, actual measured duration, checkpoint attempts, and independently recomputed prediction metrics are distinct report fields. Repeated training here measures endurance, not scientific novelty. The soak monitor performs real arithmetic tasks, API reads and durable writes at intervals. Keep the monitor running; sleeping a machine does not create observed service coverage.

Use a unique `--report` path for a new experiment. Existing reports are protected from accidental replacement. A monitor timeout leaves the run identifier and running state for later inspection. Reconnection resumes monitoring of the existing durable server run.

## Persistent local monitoring

After submitting the compute job once, install the macOS per-user monitor service:

```sh
.venv/bin/python scripts/qualification_service.py install
.venv/bin/python scripts/qualification_service.py status
```

It attaches to the run ID already in `var/qa/release-compute.json`; it refuses to create a replacement compute job. It starts or resumes `var/qa/release-soak.json` for 86,400 observed monitoring seconds. Both monitors run under `org.forest.qualification`, independently of the browser session. The LaunchAgent starts at user login, restarts a crashed supervisor with a 60-second throttle, and resumes interrupted/error monitors after a 60-second retry delay. Completed passing or failing reports exit normally and are not rerun. Configuration errors stop without an automatic failure loop.

An operating-system lock allows one monitor per report. The soak saves its request/run identifiers before waiting, so a monitor restart reuses existing work. Queued checks remain pending rather than launching overlapping replacements. Reports retain interruptions, failures, observed segments, and large sampling gaps; gaps between monitor sessions do not contribute to the requested duration. The service does not change sleep settings or keep the computer awake. Sleep, logout, shutdown, and API disconnection can delay completion and must be considered when interpreting coverage.

Monitor status and logs are in `var/qa/qualification-service/`. Removing this monitor service preserves server tasks and reports:

```sh
.venv/bin/python scripts/qualification_service.py remove
```

For Linux or foreground supervision, run `python scripts/qualification_service.py run` in the configured Python environment and keep that process alive. Service-level persistence on Linux is not provided by this helper. All monitor output stays under `var/`, which source packaging excludes.

## Reporting results

Machine-readable reports default to `var/qa/release-{agents,graph,compute,soak}.json`. Agent artifacts are copied alongside them under `release-artifacts/`. Reports distinguish passing, failing, still running, budget-blocked and monitoring-error states. They retain failed attempts, actual run IDs, timing and provider usage. Do not copy runtime databases or provider settings into a public evidence bundle.

A release report should include: environment and revision, provider/model and spend assumptions, exact selected task IDs, first-attempt and retry outcomes, observed durations, failures, and untested boundaries. Fair comparisons require the same tasks, inputs, settings and resources, followed by actual reruns and independent recomputation.

## Source release

```sh
.venv/bin/python scripts/package_source.py --output output/forest-source.zip
# Or create a new directory containing only public source:
.venv/bin/python scripts/package_source.py --directory ../forest-github
```

The source-only archive uses explicit source locations, excludes runtime data, environments, credentials and symlinks, and rejects common embedded secret patterns. Inspect the final archive before publishing. Imported documents and dependency licenses remain their owners' responsibility; FOREST's Apache-2.0 license covers its original source.
