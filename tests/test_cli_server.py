"""Exercise supervision and install contracts without starting user services."""
from __future__ import annotations

import os
from pathlib import Path
import plistlib
import select
import signal
import subprocess
import sys
from types import SimpleNamespace

import pytest

from forest_cli import server
from scripts import install, service, start
from services.api.config_paths import resolve_database_url


@pytest.fixture
def clean_server_env(monkeypatch):
    for name in ("FOREST_DATA_DIR", "FOREST_DATABASE_URL"):
        monkeypatch.delenv(name, raising=False)


class Child:
    def __init__(self, exit_code=None, timeout=False):
        self.exit_code = exit_code
        self.timeout = timeout
        self.terminated = False
        self.killed = False
        self.waited = []

    def poll(self):
        return self.exit_code

    def terminate(self):
        self.terminated = True

    def kill(self):
        self.killed = True
        self.exit_code = -9

    def wait(self, timeout):
        self.waited.append(timeout)
        if self.timeout and not self.killed:
            raise subprocess.TimeoutExpired("child", timeout)
        return self.exit_code or 0


@pytest.fixture
def supervised(monkeypatch, tmp_path):
    calls = SimpleNamespace(run=[], spawn=[], children=[], signals=[])
    monkeypatch.setattr(server, "ROOT", tmp_path)

    def run(command, **kwargs):
        calls.run.append((command, kwargs))
        return subprocess.CompletedProcess(command, 0)

    def spawn(command, **kwargs):
        child = Child()
        calls.spawn.append((command, kwargs))
        calls.children.append(child)
        return child

    def replace_signal(signum, handler):
        calls.signals.append((signum, handler))
        return signal.SIG_DFL

    def interrupt(_):
        raise KeyboardInterrupt

    monkeypatch.setattr(server.subprocess, "run", run)
    monkeypatch.setattr(server.subprocess, "Popen", spawn)
    monkeypatch.setattr(server.signal, "signal", replace_signal)
    monkeypatch.setattr(server.time, "sleep", interrupt)
    return calls


def test_server_import_has_no_api_or_data_side_effects(tmp_path):
    data = tmp_path / "uncreated"
    env = {**os.environ, "FOREST_DATA_DIR": str(data), "PYTHONPATH": str(Path(__file__).resolve().parents[1])}
    result = subprocess.run(
        [sys.executable, "-c", "import sys, forest_cli.server; assert 'services.api.db' not in sys.modules; assert 'services.api.config' not in sys.modules"],
        cwd=tmp_path, env=env, capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    assert not data.exists()


def test_data_directory_selects_matching_sqlite_without_mutating_env(clean_server_env, tmp_path):
    directory = tmp_path / "new data"
    env = server.server_environment(host="127.0.0.1", port=8123, data_dir=directory)
    assert env["FOREST_DATA_DIR"] == str(directory)
    assert env["FOREST_DATABASE_URL"] == "sqlite:///" + str(directory / "forest.db")
    assert env["FOREST_HOST"] == "127.0.0.1" and env["FOREST_PORT"] == "8123"
    assert "FOREST_DATA_DIR" not in os.environ
    assert not directory.exists()


def test_data_environment_and_explicit_database_priority(clean_server_env, monkeypatch, tmp_path):
    monkeypatch.setenv("FOREST_DATA_DIR", str(tmp_path))
    assert server.server_environment(host="localhost", port=8000)["FOREST_DATABASE_URL"] == "sqlite:///" + str(tmp_path / "forest.db")
    monkeypatch.setenv("FOREST_DATABASE_URL", "postgresql://localhost/envdb")
    assert server.server_environment(host="localhost", port=8000, data_dir=tmp_path / "elsewhere")["FOREST_DATABASE_URL"] == "postgresql://localhost/envdb"
    assert server.server_environment(host="localhost", port=8000, database_url="sqlite:///explicit.db")["FOREST_DATABASE_URL"] == "sqlite:///explicit.db"


def test_api_default_database_follows_configured_data_dir(tmp_path):
    directory = tmp_path / "writable data"
    assert resolve_database_url("", False, directory) == "sqlite:///" + str(directory / "forest.db")
    assert resolve_database_url("postgresql://example/db", True, directory) == "postgresql://example/db"


@pytest.mark.parametrize("system,xdg,relative", [("darwin", None, "Library/Application Support/FOREST"), ("linux", None, ".local/share/forest"), ("linux", "xdg-data", "xdg-data/forest")])
def test_installed_server_defaults_to_user_data(clean_server_env, monkeypatch, tmp_path, system, xdg, relative):
    monkeypatch.setattr(server, "ROOT", tmp_path / "site-packages")
    monkeypatch.setattr(sys, "platform", system)
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
    if xdg:
        monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / xdg))
    else:
        monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    env = server.server_environment(host="localhost", port=8000)
    assert env["FOREST_DATA_DIR"] == str(tmp_path / relative)
    assert env["FOREST_DATABASE_URL"] == "sqlite:///" + str(tmp_path / relative / "forest.db")
    assert not (tmp_path / relative).exists()


def test_source_server_keeps_existing_default_configuration(clean_server_env, monkeypatch, tmp_path):
    (tmp_path / "pyproject.toml").touch()
    (tmp_path / "research").mkdir()
    monkeypatch.setattr(server, "ROOT", tmp_path)
    env = server.server_environment(host="localhost", port=8000)
    assert "FOREST_DATA_DIR" not in env and "FOREST_DATABASE_URL" not in env


def test_headless_launches_only_api_and_worker_and_cleans_up(supervised, monkeypatch, clean_server_env, tmp_path):
    monkeypatch.setattr(server.shutil, "which", lambda _: pytest.fail("Headless startup must not inspect Node/npm"))
    data = tmp_path / "data"
    assert server.serve(headless=True, port=8123, data_dir=data) == 0
    assert [command[:3] for command, _ in supervised.run] == [[sys.executable, "-m", "services.api.db"]]
    assert supervised.spawn[0][0] == [sys.executable, "-m", "uvicorn", "services.api.main:app", "--host", "127.0.0.1", "--port", "8123", "--timeout-graceful-shutdown", "5"]
    assert supervised.spawn[1][0] == [sys.executable, "-m", "services.worker.main"]
    assert all(options["env"]["FOREST_DATABASE_URL"] == "sqlite:///" + str(data / "forest.db") for _, options in supervised.run + supervised.spawn)
    assert all(child.terminated and child.waited == [10] for child in supervised.children)
    assert supervised.signals[-2:] == [(signal.SIGINT, signal.SIG_DFL), (signal.SIGTERM, signal.SIG_DFL)]


@pytest.mark.skipif(os.name == "nt", reason="forest serve requires POSIX services")
def test_sigterm_stops_the_database_initializer(tmp_path):
    supervisor = '''
import subprocess
import sys

from forest_cli import server

run = subprocess.run
initializer = "import sys; print('initializer started', flush=True); sys.stdin.read()"

def run_initializer(command, **kwargs):
    assert command[1:] == ["-m", "services.api.db"]
    return run([sys.executable, "-c", initializer], **kwargs)

server.subprocess.run = run_initializer
raise SystemExit(server.serve(headless=True, data_dir=sys.argv[1]))
'''
    process = subprocess.Popen(
        [sys.executable, "-c", supervisor, str(tmp_path / "data")],
        cwd=server.ROOT, stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True,
    )
    try:
        assert select.select([process.stdout], [], [], 5)[0], "initializer never started"
        assert process.stdout.readline().strip() == "initializer started"

        process.terminate()
        process.wait(timeout=5)

        # The pipe stays open if the initializer outlives its supervisor.
        assert select.select([process.stdout], [], [], 5)[0], "initializer survived SIGTERM"
        assert process.stdout.read() == ""
        assert process.returncode == 0
    finally:
        process.stdin.close()
        if process.poll() is None:
            process.kill()
        process.wait(timeout=5)
        process.stdout.close()


def test_child_failure_propagates_exit_and_stops_peer(supervised, monkeypatch):
    def spawn(command, **kwargs):
        child = Child(exit_code=7 if not supervised.children else None)
        supervised.children.append(child)
        return child
    monkeypatch.setattr(server.subprocess, "Popen", spawn)
    assert server.serve(headless=True) == 7
    assert not supervised.children[0].terminated
    assert supervised.children[1].terminated


def test_partial_launch_failure_cleans_existing_child(supervised, monkeypatch):
    def spawn(command, **kwargs):
        if supervised.children:
            raise OSError("worker launch failed")
        child = Child()
        supervised.children.append(child)
        return child
    monkeypatch.setattr(server.subprocess, "Popen", spawn)
    assert server.serve(headless=True) == 1
    assert supervised.children[0].terminated


def test_unresponsive_children_are_killed_then_reaped():
    child = Child(timeout=True)
    server._stop_children([child])
    assert child.terminated and child.killed and child.waited == [10, 5]


def test_web_build_and_dev_preserve_legacy_behavior(supervised, monkeypatch, tmp_path):
    web = tmp_path / "apps/web"
    (web / "node_modules").mkdir(parents=True)
    (web / "package.json").write_text("{}")
    monkeypatch.setattr(server.shutil, "which", lambda _: "/usr/bin/npm")
    assert server.serve() == 0
    assert supervised.run[0][0] == ["npm", "run", "build"]
    assert supervised.run[0][1]["cwd"] == web
    supervised.run.clear()
    supervised.spawn.clear()
    assert server.serve(dev=True) == 0
    assert all(command[0] != "npm" for command, _ in supervised.run)
    assert supervised.spawn[-1][0] == ["npm", "run", "dev", "--", "--host", "127.0.0.1"]
    assert supervised.spawn[-1][1]["cwd"] == web


def test_existing_web_assets_need_no_node(supervised, monkeypatch, tmp_path):
    index = tmp_path / "apps/web/dist/index.html"
    index.parent.mkdir(parents=True)
    index.write_text("web")
    monkeypatch.setattr(server.shutil, "which", lambda _: pytest.fail("Built Web assets need no npm"))
    assert server.serve() == 0


def test_missing_web_returns_actionable_error_before_database(supervised, capsys):
    assert server.serve() == 1
    assert "--headless" in capsys.readouterr().err
    assert supervised.run == [] and supervised.spawn == []


def test_invalid_mode_or_port_launches_nothing(supervised):
    assert server.serve(headless=True, dev=True) == 2
    assert server.serve(headless=True, port=0) == 2
    assert supervised.run == [] and supervised.spawn == []


def test_legacy_start_checks_virtualenv_and_forwards_options(monkeypatch, tmp_path):
    monkeypatch.setattr(start, "ROOT", tmp_path)
    python = tmp_path / ".venv/bin/python"
    python.parent.mkdir(parents=True)
    python.touch()
    monkeypatch.setattr(sys, "executable", str(python))
    monkeypatch.setattr(sys, "argv", ["start.py", "--headless", "--port", "8123", "--data-dir", str(tmp_path / "data"), "--database-url", "sqlite:///explicit.db"])
    seen = {}
    def fake_serve(**kwargs):
        seen.update(kwargs)
        return 3
    monkeypatch.setattr(start, "serve", fake_serve)
    assert start.main() == 3
    assert seen == {"host": "127.0.0.1", "port": 8123, "data_dir": tmp_path / "data", "database_url": "sqlite:///explicit.db", "headless": True, "dev": False}


def test_legacy_start_reexecs_into_project_virtualenv(monkeypatch, tmp_path):
    monkeypatch.setattr(start, "ROOT", tmp_path)
    python = tmp_path / ".venv/bin/python"
    python.parent.mkdir(parents=True)
    python.touch()
    monkeypatch.setattr(sys, "argv", ["start.py", "--headless"])
    recorded = []
    def execv(program, arguments):
        recorded.append((program, arguments))
        raise RuntimeError("reexec")
    monkeypatch.setattr(os, "execv", execv)
    with pytest.raises(RuntimeError, match="reexec"):
        start.main()
    assert recorded == [(str(python), [str(python), str(tmp_path / "scripts/start.py"), "--headless"])]


def test_legacy_start_missing_virtualenv_explains_install(monkeypatch, tmp_path):
    monkeypatch.setattr(start, "ROOT", tmp_path)
    monkeypatch.setattr(sys, "argv", ["start.py", "--headless"])
    with pytest.raises(SystemExit, match="install.py"):
        start.main()


@pytest.mark.parametrize("headless", [True, False])
def test_local_install_registers_entry_and_skips_web_only_when_requested(monkeypatch, tmp_path, headless):
    monkeypatch.setattr(install, "ROOT", tmp_path)
    monkeypatch.setattr(sys, "argv", ["install.py"] + (["--headless"] if headless else []))
    calls = []
    def which(name):
        if headless:
            pytest.fail("Headless install must not inspect Node/npm")
        return "/usr/bin/" + name
    monkeypatch.setattr(install.shutil, "which", which)
    monkeypatch.setattr(install.subprocess, "check_output", lambda *a, **k: "v22.0.0\n")
    monkeypatch.setattr(install.subprocess, "run", lambda command, **kwargs: calls.append((command, kwargs)))
    install.main()
    python = str(tmp_path / ".venv/bin/python")
    assert ([python, "-m", "pip", "install", "--no-deps", "-e", str(tmp_path)], {"check": True}) in calls
    npm = [command for command, _ in calls if command[0] == "npm"]
    assert npm == ([] if headless else [["npm", "ci"], ["npm", "run", "build"]])
    assert calls[-1][0] == [python, "-m", "services.api.db"]


@pytest.mark.parametrize("system", ["Darwin", "Linux"])
def test_service_render_preserves_headless_and_data_directory(monkeypatch, tmp_path, system):
    monkeypatch.setattr(service, "ROOT", tmp_path)
    monkeypatch.setattr(service.platform, "system", lambda: system)
    directory = tmp_path / "custom data"
    target = service.render(8123, headless=True, data_dir=directory)
    if system == "Darwin":
        with target.open("rb") as stream:
            data = plistlib.load(stream)
        assert data["ProgramArguments"][-5:] == ["--port", "8123", "--headless", "--data-dir", str(directory)]
    else:
        text = target.read_text()
        assert '"--headless" "--data-dir" "' + str(directory) + '"' in text
        assert '"--port" "8123"' in text


@pytest.mark.parametrize("action", ["status", "remove"])
def test_service_status_remove_do_not_render_or_create_files(monkeypatch, tmp_path, action):
    monkeypatch.setattr(service, "ROOT", tmp_path)
    monkeypatch.setattr(service.platform, "system", lambda: "Linux")
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path / "home"))
    monkeypatch.setattr(sys, "argv", ["service.py", action, "--headless", "--data-dir", str(tmp_path / "data")])
    monkeypatch.setattr(service, "render", lambda *a, **k: pytest.fail("Read/remove actions must not render"))
    monkeypatch.setattr(service.subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a[0], 0))
    service.main()
    assert not (tmp_path / "var").exists() and not (tmp_path / "data").exists()


@pytest.mark.parametrize("system", ["Darwin", "Linux"])
@pytest.mark.parametrize("exit_code", [0, 1])
def test_service_remove_preserves_unit_until_manager_succeeds(monkeypatch, tmp_path, system, exit_code):
    monkeypatch.setattr(service, "ROOT", tmp_path)
    monkeypatch.setattr(service.platform, "system", lambda: system)
    home = tmp_path / "home"
    monkeypatch.setattr(Path, "home", staticmethod(lambda: home))
    monkeypatch.setattr(sys, "argv", ["service.py", "remove"])
    relative = "Library/LaunchAgents/org.forest.research.plist" if system == "Darwin" else ".config/systemd/user/forest.service"
    installed = home / relative
    installed.parent.mkdir(parents=True)
    original = b"installed service configuration\n"
    installed.write_bytes(original)
    expected = (
        ["launchctl", "bootout", "gui/" + str(os.getuid()), str(installed)]
        if system == "Darwin"
        else ["systemctl", "--user", "disable", "--now", "forest.service"]
    )
    real_run = subprocess.run
    calls = []

    def manager(command, **kwargs):
        calls.append(command)
        assert command == expected
        assert installed.read_bytes() == original
        # Exercise real subprocess exit handling without touching a user service.
        return real_run([sys.executable, "-c", f"raise SystemExit({exit_code})"], **kwargs)

    monkeypatch.setattr(service.subprocess, "run", manager)
    if exit_code:
        with pytest.raises(subprocess.CalledProcessError) as error:
            service.main()
        assert error.value.returncode == exit_code
        assert installed.read_bytes() == original
    else:
        service.main()
        assert not installed.exists()
    assert calls == [expected]
