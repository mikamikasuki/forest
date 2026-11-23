"""Foreground API/worker supervisor, with no API imports or durable client state."""
from __future__ import annotations

import os
from pathlib import Path
import signal
import shutil
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]


def _default_installed_data_dir() -> Path:
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "FOREST"
    return Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share") / "forest"


def server_environment(*, host: str, port: int, data_dir=None, database_url=None) -> dict[str, str]:
    """Give both services the same configuration without mutating this process."""
    env = {**os.environ, "PYTHONUNBUFFERED": "1", "FOREST_HOST": host, "FOREST_PORT": str(port)}
    env["PYTHONPATH"] = os.pathsep.join(filter(None, (str(ROOT), env.get("PYTHONPATH", ""))))
    directory = data_dir if data_dir is not None else env.get("FOREST_DATA_DIR") or None
    source_checkout = (ROOT / "pyproject.toml").is_file() and (ROOT / "research").is_dir()
    if directory is None and not source_checkout:
        # Wheels live in site-packages, which must never become a data store.
        # Editable/source installs retain the existing source var/ convention.
        directory = _default_installed_data_dir()
    if directory is not None:
        directory = Path(directory).expanduser().resolve()
        env["FOREST_DATA_DIR"] = str(directory)
        if database_url is None and not env.get("FOREST_DATABASE_URL"):
            env["FOREST_DATABASE_URL"] = "sqlite:///" + str(directory / "forest.db")
    if database_url is not None:
        env["FOREST_DATABASE_URL"] = database_url
    return env


def _prepare_web(*, headless: bool, dev: bool, env: dict[str, str]) -> None:
    if headless:
        return
    web = ROOT / "apps" / "web"
    if not dev and (web / "dist" / "index.html").is_file():
        return
    if not (web / "package.json").is_file():
        raise RuntimeError("Web assets are unavailable. Use forest serve --headless, or run from a source checkout with the Web app installed.")
    if not shutil.which("npm"):
        raise RuntimeError("Node.js/npm is required for the Web app. Use forest serve --headless, or run python scripts/install.py to install the Web dependencies.")
    if not (web / "node_modules").is_dir():
        raise RuntimeError("Web dependencies are missing. Run python scripts/install.py, or use forest serve --headless.")
    if not dev:
        subprocess.run(["npm", "run", "build"], cwd=web, env=env, check=True)


def _stop_children(children) -> None:
    # Worker shutdown preserves detached jobs for the next worker's recovery.
    # Do not signal a process group containing those jobs.
    for child in children:
        if child.poll() is None:
            child.terminate()
    for child in children:
        try:
            child.wait(timeout=10)
        except subprocess.TimeoutExpired:
            child.kill()
            child.wait(timeout=5)


def serve(*, host="127.0.0.1", port=8000, data_dir=None, database_url=None, headless=False, dev=False) -> int:
    """Run independent API and worker processes until interrupted or one exits."""
    if os.name == "nt":
        print("Run FOREST services inside WSL2, or use Compose with Docker Desktop; native Windows services are unavailable.", file=sys.stderr)
        return 1
    if headless and dev:
        print("--headless and --dev cannot be used together.", file=sys.stderr)
        return 2
    if not 1 <= port <= 65535:
        print("Port must be between 1 and 65535.", file=sys.stderr)
        return 2
    env = server_environment(host=host, port=port, data_dir=data_dir, database_url=database_url)
    children = []
    previous_handlers = {}
    interrupted = False

    def stop(signum, frame):
        raise KeyboardInterrupt

    try:
        _prepare_web(headless=headless, dev=dev, env=env)
        for signum in (signal.SIGINT, signal.SIGTERM):
            previous_handlers[signum] = signal.signal(signum, stop)
        # Importing the CLI/help must never create a database. Initialization is
        # confined to an explicit serve invocation in a separate interpreter.
        subprocess.run([sys.executable, "-m", "services.api.db"], cwd=ROOT, env=env, check=True)
        commands = [
            ([sys.executable, "-m", "uvicorn", "services.api.main:app", "--host", host, "--port", str(port), "--timeout-graceful-shutdown", "5"], ROOT),
            ([sys.executable, "-m", "services.worker.main"], ROOT),
        ]
        if dev:
            commands.append((["npm", "run", "dev", "--", "--host", host], ROOT / "apps" / "web"))
        for command, cwd in commands:
            children.append(subprocess.Popen(command, cwd=cwd, env=env))
        endpoint_host = f"[{host}]" if ":" in host else host
        print(f"FOREST API: http://{endpoint_host}:{port}", flush=True)
        if dev:
            print(f"FOREST Web: http://{endpoint_host}:5173", flush=True)
        elif headless:
            print("Headless services running. Press Ctrl+C to stop API and worker.", flush=True)
        while all(child.poll() is None for child in children):
            time.sleep(0.5)
        for child in children:
            code = child.poll()
            if code is not None:
                print(f"A FOREST service exited (code {code}); stopping the remaining services.", file=sys.stderr)
                return code if code > 0 else 1
    except KeyboardInterrupt:
        interrupted = True
    except (OSError, RuntimeError, subprocess.CalledProcessError) as exc:
        print(f"Unable to start FOREST: {exc}", file=sys.stderr)
        return 1
    finally:
        # A second Ctrl+C should not skip waiting for the children we launched.
        for signum in previous_handlers:
            signal.signal(signum, signal.SIG_IGN)
        try:
            _stop_children(children)
        finally:
            for signum, handler in previous_handlers.items():
                signal.signal(signum, handler)
    return 0 if interrupted else 1
