# Self-hosted deployment

For observer startup, source bounds, optional narration and restore behavior, see
[Live progress and source inspection](LIVE_REPORTING.md).

FOREST supports a local service on macOS and Linux, plus a PostgreSQL deployment with Compose. On Windows, run services in WSL2 or Docker Desktop. Native Windows process groups are not implemented. Source and project ZIPs use portable relative paths; `scripts/release.py --check-only` checks Windows filenames, case collisions and archive integrity.

The existing `local` execution backend runs trusted code as the host user. Select `execution_backend: "container"` for workspace isolation. There is no automatic fallback from a container error to local execution.

## Local service

Use Python 3.11+ and Node.js 22+. Run from the source directory:

```sh
python3 scripts/install.py
.venv/bin/python scripts/doctor.py --require local
.venv/bin/python scripts/start.py
```

Open `http://127.0.0.1:8000`. Configure providers in Settings. Installing FOREST does not authorize paid calls. LaTeX must be installed separately for local PDF compilation; the diagnostic reports whether `tectonic` or `pdflatex` is visible on PATH. The Compose runtime image includes LaTeX and PostgreSQL 17 dump/restore clients.

Install a persistent per-user service only when wanted:

```sh
.venv/bin/python scripts/service.py install
.venv/bin/python scripts/service.py status
```

The service renderer supports macOS LaunchAgents and Linux systemd user services. `remove` stops and unregisters that service without deleting research data. macOS user services run in the signed-in user's session; this is not a system-wide boot daemon.

## Compose with PostgreSQL

Install and start Docker Desktop or a Docker engine. macOS can also use Colima. The installer does not install or start a VM.

```sh
python3 scripts/install.py --mode compose
```

The installer creates independent random database and owner credentials under `var/deployment/secrets`, validates Compose, builds the image, and waits for service health. It preserves existing secret values. The secret directory is owner-only; its secret files are read-only and readable through Compose's secret mounts by the nonroot application user. Do not move these files into a public directory. Use `--secrets-dir` to choose another private location, then set `FOREST_SECRETS_DIR` to that absolute directory for later Compose commands. The installer accepts the standalone `docker-compose` command when the CLI plugin is unavailable.

API and worker run as UID/GID 10001, with read-only image files, writable temporary storage, dropped capabilities and no-new-privileges. A short initialization service assigns ownership of the new research volume. PostgreSQL and research files persist in separate named volumes. API access binds only to loopback by default. `FOREST_PORT` selects a different host port.

```sh
docker compose ps
docker compose logs --tail 100 api worker
docker compose stop api worker
```

Changing the database password file does not change an already initialized PostgreSQL role. Rotate the role password and the mounted secret together. An old volume created by a different UID requires an explicit ownership migration before the nonroot service can write it.

Compose services deliberately have no Docker socket. This deployment can run trusted local work inside the worker service; per-task container execution uses a host worker with access to the Docker engine and the same workspace paths. Do not mount a Docker socket into task containers. Do not expose the service publicly without configuring owner access and an appropriate HTTPS reverse proxy.

## Isolated task execution

Build the task image and verify the engine:

```sh
docker build --target task -t forest-task:local .
.venv/bin/python scripts/doctor.py --require container
```

Set this node execution configuration:

```json
{
  "execution_backend": "container",
  "container": {
    "image": "forest-task:local",
    "cpus": 1,
    "memory": "2g",
    "network": "none",
    "max_processes": 1
  }
}
```

Commands and experiment commands run inside the task image. Agent shell/Python processes use the same backend; provider requests and orchestration stay with the host service. Commands must use executables installed in the image, such as `python`; host absolute executable paths are not available. Agent Python calls map the current interpreter to the image's Python. Install task-specific dependencies in a derived image. Arbitrary Python entrypoint imports are not silently executed on the host when container mode is selected.

Each task container has one writable workspace bind mount, a read-only root, a temporary `/tmp`, CPU and memory limits, a process-count limit, no host networking, no platform database mount, no Docker socket and no inherited host environment. Explicit task environment values are allowed; platform database/owner/Docker connection variables are rejected. Container control receipts live outside the mounted workspace. Local file tools remain restricted to the task workspace by the application.

Network defaults to `none`. Explicit `network: "bridge"` permits ordinary outbound connectivity and should be used only when the computation needs it. GPU requests accept `gpus: 1` or `gpus: "all"` on a supported Linux Docker host with its GPU runtime. macOS Apple GPU passthrough is not provided. Verify GPU access and resource limits on the intended execution host before scheduling GPU work.

`max_processes` limits concurrently detached Agent containers per run. Worker admission reserves the configured CPU/memory/GPU allowance before admitting work. Its capacity defaults to host CPU/RAM; for Colima or Docker Desktop, set `FOREST_WORKER_CPU_SLOTS` and `FOREST_WORKER_MEMORY_GB` no higher than the Docker VM allocation, and set `FOREST_WORKER_GPU_SLOTS` only for available GPUs. An unavailable engine produces an explicit error, not simulated results. Runtime `cpu_seconds` and `host_process_cpu_seconds` sum observed host processes, live descendants and reported reaped-child CPU counters. On macOS, the worker reads the reaped-child counters from the kernel. When `reaped_child_cpu_available` is false, the host total may omit reaped-child work because at least one process had no accessible counter during sampling. For Docker tasks these measure only the host executor; `container_cpu_seconds` remains unavailable, and the UI must not present host executor CPU as measured container training CPU.

Live output is read from Docker's real stdout/stderr. Logs have a bounded retention policy of three 20 MB files per container; a read offset reset is reported if retention has removed earlier bytes. A run's `container_task.json` records the actual container ID and limits, and `container_result.json` records the observed terminal state. Agent process receipts are under the run's `.forest-container-processes/<workspace-name>` directory.

Containers remain detached when an API, worker or executor process dies. A restarted worker reconnects to a retained identity-checked container and collects its real result without launching the computation again. Pause/unpause controls the container, and cancellation unpauses if necessary then stops it. Completed containers are retained for inspection; remove them explicitly with Docker after preserving needed logs. A removed container has `lost` status; success is never inferred from an output file. A failed container requires a new run unless the task explicitly enables checkpoint recovery and writes a valid checkpoint. For example:

```json
{
  "recovery": {
    "mode": "checkpoint",
    "checkpoint": "checkpoint.json",
    "resume_command": ["python", "train.py", "--resume"],
    "retry_on_failure": true,
    "max_attempts": 3
  }
}
```

The task receives `FOREST_CHECKPOINT_PATH` and, on continuation, `FOREST_RESUME_PATH`, both under `/workspace`. It must save and restore its own model, optimizer, random state and iteration state as required by its computation. FOREST checks the configured file exists and is nonempty (and parses JSON checkpoints), preserves previous container receipts under attempt history, and starts a distinct attempt container using the same workspace. The configured `resume_command` is used when present; otherwise the original command must handle `FOREST_RESUME_PATH`. Retained running or completed containers are reattached, never duplicated. Cancelled work is not automatically resumed. This mechanism does not infer that arbitrary code has saved enough scientific state to resume correctly.

## Backups and recovery

Stop API, workers and any detached writers first. `--offline` confirms this requirement; the script also rejects recognized live FOREST processes using the chosen data directory. A detached container must be stopped separately. SQLite's database copy is consistent through the SQLite backup API, but an entire directory of editable files cannot be transactionally backed up while other programs modify it.

```sh
.venv/bin/python scripts/backup.py create /private-backups/forest.tar.gz \
  --data-dir /path/to/forest-data --offline
.venv/bin/python scripts/backup.py verify /private-backups/forest.tar.gz
.venv/bin/python scripts/restore.py /private-backups/forest.tar.gz \
  --data-dir /path/to/empty-restored-data
```

For PostgreSQL, pass `--database-url` on create, preferably via `FOREST_DATABASE_URL` so credentials are not placed in shell history. `pg_dump` and `pg_restore` client versions must support the server. Use `FOREST_BACKUP_POSTGRES_ADMIN_URL` or `--postgres-admin-url` for verify/restore; this connection must be authorized to create databases. PostgreSQL verify performs a real restore into a random `forest_restore_*` database, checks table row counts and removes that temporary database. Restore keeps a newly generated database and reports its name. Existing databases are never overwritten. SQLite verification also checks database integrity and foreign keys.

The backup archive includes database state, editable research files and a readable manifest. Credentials such as `secrets.json`, owner tokens and deployment secret files are omitted by default; restore provider credentials separately. `--include-secrets` explicitly includes them, so store the resulting archive privately. Archives are written with owner-only permissions. The verification checks structural integrity and inventory, not scientific correctness.

A restored destination must be empty. Restored active/queued runs become interrupted, worker records are cleared, and research controllers are paused. Review the restored content and start a new run explicitly; restored process IDs must not control old work. Configure `FOREST_DATA_DIR` and `FOREST_DATABASE_URL` for the new location before starting services. Absolute custom SSH paths and external datasets remain the operator's responsibility.

For Compose, stop API and worker, keep PostgreSQL available for `pg_dump`, and run the backup utility through a one-off application container with the research volume plus a private output mount. Set the PostgreSQL admin URL for an isolated verification environment; do not publish its credentials. The mounted-secret entrypoint supplies the ordinary application database URL to the backup command. Named-volume directory copies without a database dump are not a PostgreSQL backup.

## Release qualification

```sh
FOREST_TEST_POSTGRES=1 FOREST_TEST_DOCKER=1 .venv/bin/python -m pytest -q tests/test_deployment.py
.venv/bin/python scripts/release.py /path/to/forest-source.zip
.venv/bin/python scripts/release.py /path/to/project-export.zip --check-only
```

The opt-in tests use real PostgreSQL databases, a real Docker engine and actual child computations. Without those environment flags they are skipped. The suite checks workspace isolation, resource configuration, live output, cancellation, pause/resume, worker reconnection, explicitly checkpointed computation, backup restore and portable archive paths. Run the applicable checks on the intended host; archive portability and runtime support are separate checks.
