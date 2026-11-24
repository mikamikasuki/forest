"""Observe an actual busy child while its parent waits, without mocked counters."""
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
import psutil
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'research' / 'agents'))
from research.agents.process_host import process_group_exists
from runners.local import process_tree_resources


def test_host_cpu_includes_a_busy_child(tmp_path):
    marker = tmp_path / 'ready.json'
    child = "import time,json,pathlib,os;start=time.process_time();\nwhile time.process_time()-start<0.7: sum(i*i for i in range(10000))\npathlib.Path(" + repr(str(marker)) + ").write_text(json.dumps({'pid':os.getpid(),'cpu_seconds':time.process_time()}));time.sleep(20)"
    parent = "import subprocess,sys;subprocess.run([sys.executable,'-c'," + repr(child) + "])"
    process = subprocess.Popen([sys.executable, '-c', parent], start_new_session=True)
    try:
        deadline = time.monotonic() + 10
        while not marker.exists() and time.monotonic() < deadline:
            time.sleep(.05)
        receipt = json.loads(marker.read_text())
        resources = process_tree_resources(process.pid)
        assert resources['observed_host_processes'] >= 2
        assert resources['cpu_seconds'] >= .6
        assert resources['host_process_cpu_seconds'] == resources['cpu_seconds']
        assert resources['rss_bytes'] > 0
    finally:
        import os, signal
        os.killpg(process.pid, signal.SIGKILL)
        process.wait(timeout=5)


@pytest.mark.skipif(sys.platform not in ('linux', 'darwin'), reason='requires reaped child CPU counters')
def test_host_cpu_keeps_a_child_after_its_parent_waits(tmp_path):
    receipt = tmp_path / 'reaped.json'
    child = '''
import time
start = time.process_time()
while time.process_time() - start < 0.55:
    sum(i * i for i in range(10000))
'''
    parent = '''
import json, pathlib, resource, subprocess, sys
subprocess.run([sys.executable, '-c', sys.argv[2]], check=True)
usage = resource.getrusage(resource.RUSAGE_CHILDREN)
receipt = pathlib.Path(sys.argv[1])
pending = receipt.with_suffix('.tmp')
pending.write_text(json.dumps(usage.ru_utime + usage.ru_stime))
pending.replace(receipt)
input()
'''
    process = subprocess.Popen(
        [sys.executable, '-c', parent, str(receipt), child],
        stdin=subprocess.PIPE, start_new_session=True,
    )
    try:
        deadline = time.monotonic() + 10
        while not receipt.exists() and process.poll() is None and time.monotonic() < deadline:
            time.sleep(.02)
        assert receipt.exists(), 'parent did not finish waiting for its child'
        expected_child_cpu = json.loads(receipt.read_text())
        assert expected_child_cpu >= .5

        resources = process_tree_resources(process.pid)
        assert resources['observed_host_processes'] == 1
        assert expected_child_cpu - .05 <= resources['cpu_seconds'] < expected_child_cpu + .25
        assert resources['host_process_cpu_seconds'] == resources['cpu_seconds']
        assert resources['reaped_child_cpu_available'] is True
    finally:
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGKILL)
        process.wait(timeout=5)
        process.stdin.close()


@pytest.mark.skipif(not hasattr(os, 'fork'), reason='requires POSIX process groups')
def test_zombie_only_process_group_is_treated_as_terminated():
    ready_read, ready_write = os.pipe()
    child = os.fork()
    if child == 0:
        os.close(ready_read)
        os.setpgid(0, 0)
        os.write(ready_write, b'group-ready')
        os.close(ready_write)
        os._exit(0)

    os.close(ready_write)
    try:
        assert os.read(ready_read, len(b'group-ready')) == b'group-ready'
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            try:
                if psutil.Process(child).status() == psutil.STATUS_ZOMBIE:
                    break
            except psutil.NoSuchProcess:
                break
            time.sleep(.01)
        assert psutil.Process(child).status() == psutil.STATUS_ZOMBIE
        assert not process_group_exists(child)
    finally:
        os.close(ready_read)
        os.waitpid(child, 0)
