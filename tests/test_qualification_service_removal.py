"""Exercise monitor removal without controlling any installed user service."""
import os
from pathlib import Path
import subprocess
import sys

import pytest

from scripts import qualification_service as service


@pytest.mark.parametrize('exit_code', [0, 1, 5])
def test_remove_requires_successful_bootout(monkeypatch, tmp_path, capsys, exit_code):
    monkeypatch.setattr(service.platform, 'system', lambda: 'Darwin')
    monkeypatch.setattr(Path, 'home', staticmethod(lambda: tmp_path))
    target = tmp_path / 'Library/LaunchAgents' / f'{service.LABEL}.plist'
    target.parent.mkdir(parents=True)
    original = b'installed monitor configuration\n'
    target.write_bytes(original)
    report = tmp_path / 'report.json'
    report.write_text('{"status":"running"}')
    real_run = subprocess.run
    calls = []

    def bootout(command, **kwargs):
        calls.append(command)
        assert command == ['launchctl', 'bootout', f'gui/{os.getuid()}', str(target)]
        assert target.read_bytes() == original
        return real_run([sys.executable, '-c', f'raise SystemExit({exit_code})'], timeout=5, **kwargs)

    monkeypatch.setattr(service.subprocess, 'run', bootout)
    if exit_code:
        with pytest.raises(subprocess.CalledProcessError) as error:
            service.main(['remove'])
        assert error.value.returncode == exit_code
        assert target.read_bytes() == original
        assert 'monitors removed' not in capsys.readouterr().out
    else:
        assert service.main(['remove']) == 0
        assert not target.exists()
        assert 'monitors removed' in capsys.readouterr().out
    assert len(calls) == 1
    assert report.read_text() == '{"status":"running"}'


def test_remove_preserves_configuration_if_launchctl_is_unavailable(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(service.platform, 'system', lambda: 'Darwin')
    monkeypatch.setattr(Path, 'home', staticmethod(lambda: tmp_path))
    target = tmp_path / 'Library/LaunchAgents' / f'{service.LABEL}.plist'
    target.parent.mkdir(parents=True)
    target.write_bytes(b'original')

    def unavailable(*args, **kwargs):
        raise FileNotFoundError('launchctl unavailable')

    monkeypatch.setattr(service.subprocess, 'run', unavailable)
    with pytest.raises(FileNotFoundError, match='launchctl unavailable'):
        service.main(['remove'])
    assert target.read_bytes() == b'original'
    assert 'monitors removed' not in capsys.readouterr().out
