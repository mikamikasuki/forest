"""Actual local process control with process identity and bounded log reads."""
import ctypes
from functools import lru_cache
import os
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path
import psutil


def process_matches(pid, created):
    try:
        p = psutil.Process(pid)
        return abs(p.create_time() - float(created or 0)) < 0.2 and p.status() != psutil.STATUS_ZOMBIE
    except (psutil.NoSuchProcess, psutil.AccessDenied, TypeError):
        return False


def signal_group(pid, created, sig):
    if not process_matches(pid, created):
        return False
    try:
        os.killpg(os.getpgid(pid), sig)
        return True
    except ProcessLookupError:
        return False


def stop_group(pid, created, grace=3):
    if not process_matches(pid, created):
        return
    try:
        group = os.getpgid(pid)
        os.killpg(group, signal.SIGTERM)
        # A stopped process cannot handle TERM until continued.
        os.killpg(group, signal.SIGCONT)
    except ProcessLookupError:
        return
    except PermissionError:
        if not process_matches(pid, created): return
        raise
    deadline = time.monotonic() + grace
    while time.monotonic() < deadline:
        try: os.killpg(group, 0)
        except ProcessLookupError: return
        except PermissionError:
            if not process_matches(pid, created): return
            raise
        time.sleep(.05)
    try: os.killpg(group, signal.SIGKILL)
    except ProcessLookupError: pass
    except PermissionError:
        if process_matches(pid, created): raise


def run_command(command, cwd, timeout=None, env=None, output_path=None, tail_bytes=1_000_000):
    """No implicit time limit; full output is spooled rather than held in memory."""
    start = time.monotonic()
    if output_path:
        target = Path(output_path)
    else:
        descriptor, generated = tempfile.mkstemp(prefix='forest-command-', suffix='.log')
        os.close(descriptor)
        target = Path(generated)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open('ab', buffering=0) as stream:
        p = subprocess.Popen(command, cwd=str(cwd), stdout=stream, stderr=subprocess.STDOUT, env=env, start_new_session=True)
        created = psutil.Process(p.pid).create_time()
        try: p.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            stop_group(p.pid, created)
            p.wait()
            raise TimeoutError(f'Command exceeded {timeout}s; output is available at {target}')
    with target.open('rb') as stream:
        stream.seek(max(0, target.stat().st_size - tail_bytes))
        output = stream.read().decode(errors='replace')
    return {'exit_code': p.returncode, 'stdout': output, 'output_path': str(target),
            'output_truncated': target.stat().st_size > tail_bytes, 'elapsed': time.monotonic() - start}


class _DarwinRusageV1(ctypes.Structure):
    _fields_ = [
        ('uuid', ctypes.c_uint8 * 16),
        ('user_time', ctypes.c_uint64),
        ('system_time', ctypes.c_uint64),
        ('other_process_counters', ctypes.c_uint64 * 8),
        ('child_user_time', ctypes.c_uint64),
        ('child_system_time', ctypes.c_uint64),
        ('other_child_counters', ctypes.c_uint64 * 4),
    ]


class _MachTimebase(ctypes.Structure):
    _fields_ = [('numer', ctypes.c_uint32), ('denom', ctypes.c_uint32)]


@lru_cache(maxsize=1)
def _darwin_cpu_reader():
    system = ctypes.CDLL(None)
    timebase = _MachTimebase()
    timebase_info = system.mach_timebase_info
    timebase_info.argtypes = [ctypes.POINTER(_MachTimebase)]
    timebase_info.restype = ctypes.c_int
    if timebase_info(ctypes.byref(timebase)) != 0:
        return None
    rusage = system.proc_pid_rusage
    rusage.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.POINTER(_DarwinRusageV1)]
    rusage.restype = ctypes.c_int
    return rusage, timebase.numer / timebase.denom / 1_000_000_000


def _process_cpu_seconds(process):
    if sys.platform == 'darwin':
        reader = _darwin_cpu_reader()
        if reader is not None:
            rusage, seconds_per_tick = reader
            counters = _DarwinRusageV1()
            # RUSAGE_INFO_V1 adds the waiting process's reaped child counters.
            if rusage(process.pid, 1, ctypes.byref(counters)) == 0:
                ticks = (counters.user_time + counters.system_time + counters.child_user_time
                         + counters.child_system_time)
                return ticks * seconds_per_tick, True
        counters = process.cpu_times()
        return counters.user + counters.system, False

    counters = process.cpu_times()
    children_user = getattr(counters, 'children_user', None)
    children_system = getattr(counters, 'children_system', None)
    seconds = counters.user + counters.system + (children_user or 0) + (children_system or 0)
    return seconds, children_user is not None and children_system is not None


def process_tree_resources(pid, created=None):
    """Observed host CPU/RSS, including live descendants and reaped children.

    CPU counters describe OS processes, not containers managed by a remote
    Docker daemon. A process disappearing during inspection is simply omitted.
    """
    parent = psutil.Process(pid)
    if created is not None and abs(parent.create_time() - float(created)) >= .2:
        raise psutil.NoSuchProcess(pid)
    processes = [parent, *parent.children(recursive=True)]
    cpu_seconds, rss_bytes, measured = 0.0, 0, 0
    reaped_child_cpu_available = True
    for process in processes:
        try:
            seconds, includes_reaped = _process_cpu_seconds(process)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            reaped_child_cpu_available = False
            continue
        # Reaped children are in the waiting process's counters; live
        # descendants are represented separately in the traversal.
        cpu_seconds += seconds
        reaped_child_cpu_available &= includes_reaped
        try:
            rss_bytes += process.memory_info().rss
            measured += 1
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return {'cpu_seconds': cpu_seconds, 'host_process_cpu_seconds': cpu_seconds,
            'rss_bytes': rss_bytes, 'observed_host_processes': measured,
            'reaped_child_cpu_available': reaped_child_cpu_available}
