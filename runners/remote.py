"""Detached SSH tasks with real receipts, log offsets, and identity checks."""
import json
import re
import shlex
import subprocess
import uuid

SUPERVISOR = r'''
import json,os,pathlib,signal,subprocess,sys,time,fcntl
spec=json.loads(sys.argv[1]); folder=pathlib.Path(spec['folder']); folder.mkdir(parents=True,exist_ok=True)
state={'task_id':spec['task_id'],'pid':os.getpid(),'process_created':subprocess.check_output(['ps','-p',str(os.getpid()),'-o','lstart='],text=True).strip(),'status':'running','started_at':time.time()}
def save():
    temporary=folder/'state.tmp'; temporary.write_text(json.dumps(state)); temporary.replace(folder/'state.json')
def stop(sig,frame):
    state.update(status='cancelled',finished_at=time.time()); save(); raise SystemExit(128+sig)
signal.signal(signal.SIGTERM,stop)
save()
with (folder/'stdout.txt').open('ab',buffering=0) as stream:
    while True:
        with (folder/'launch.lock').open('a') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX)
            if (folder/'cancel.json').exists():
                state.update(status='cancelled',finished_at=time.time()); save(); raise SystemExit(143)
            if (folder/'pause.json').exists():
                state['status']='paused'; save()
            else:
                process=subprocess.Popen(spec['argv'],cwd=spec['workdir'],env={**os.environ,**spec.get('env',{})},stdout=stream,stderr=subprocess.STDOUT)
                state.update(command_pid=process.pid,status='running'); save(); break
        time.sleep(.05)
    try:
        code=process.wait(timeout=spec.get('timeout'))
        state.update(status='completed' if code==0 else 'failed',exit_code=code)
    except subprocess.TimeoutExpired:
        state.update(status='failed',exit_code=124,error='Configured task time budget exceeded'); save()
        signal.signal(signal.SIGTERM,signal.SIG_IGN); os.killpg(os.getpgrp(),signal.SIGTERM)
        try: process.wait(timeout=3)
        except subprocess.TimeoutExpired: os.killpg(os.getpgrp(),signal.SIGKILL)
state['finished_at']=time.time(); save()
'''


class RemoteRunner:
    def __init__(self, config):
        self.config = config
        host = config.get('hostname', ''); user = config.get('username', '')
        if not re.fullmatch(r'[A-Za-z0-9_.:-]+', host) or not re.fullmatch(r'[A-Za-z0-9_.-]+', user):
            raise ValueError('A valid SSH hostname and username are required')
        self.target = f'{user}@{host}'
        self.options = ['-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=yes', '-o', 'ConnectTimeout=10', '-p', str(int(config.get('port', 22)))]
        if config.get('known_hosts_file'): self.options += ['-o', 'UserKnownHostsFile='+config['known_hosts_file']]
        if config.get('identity_file'): self.options += ['-i', config['identity_file']]

    def command(self, argv, timeout=30):
        result = subprocess.run(['ssh', *self.options, self.target, shlex.join(argv)], capture_output=True, text=True, timeout=timeout)
        if result.returncode: raise RuntimeError(result.stderr[-4000:])
        return result.stdout

    def test(self):
        return {'stdout': self.command(['python3', '-c', 'import platform,os; print(platform.platform()); print(os.getpid())']), 'connected': True}

    def start(self, argv, workdir, task_id=None, env=None, timeout=None):
        task_id = task_id or str(uuid.uuid4())
        if not re.fullmatch(r'[A-Za-z0-9_-]+', task_id): raise ValueError('Invalid task identifier')
        folder = workdir.rstrip('/') + '/.forest-tasks/' + task_id
        spec = {'task_id': task_id, 'folder': folder, 'argv': argv, 'workdir': workdir, 'env': env or {}, 'timeout': timeout}
        script = "import pathlib,subprocess,json,time,fcntl; folder=pathlib.Path(" + repr(folder) + "); folder.mkdir(parents=True,exist_ok=True); lock=(folder/'launch.lock').open('a'); fcntl.flock(lock,fcntl.LOCK_EX); state=folder/'state.json'; "
        script += "\nif (folder/'cancel.json').exists(): print((folder/'cancel.json').read_text())\nelif state.exists(): print(state.read_text())\nelse:\n"
        script += " p=subprocess.Popen(['python3','-c'," + repr(SUPERVISOR) + "," + repr(json.dumps(spec)) + "],stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,start_new_session=True)\n"
        script += " for _ in range(100):\n  if state.exists(): break\n  time.sleep(.02)\n print(state.read_text() if state.exists() else json.dumps({'task_id':" + repr(task_id) + ",'pid':p.pid,'status':'starting'}))"
        return {**json.loads(self.command(['python3', '-c', script])), 'folder': folder}

    def resolve_launch(self, job):
        """Adopt an uncertain submission under the same remote launch lock."""
        script = "import pathlib,json,fcntl; folder=pathlib.Path(" + repr(job['folder']) + "); folder.mkdir(parents=True,exist_ok=True); lock=(folder/'launch.lock').open('a'); fcntl.flock(lock,fcntl.LOCK_EX); "
        script += "p=folder/'cancel.json' if (folder/'cancel.json').exists() else folder/'state.json'; print(p.read_text() if p.exists() else 'null')"
        result = json.loads(self.command(['python3', '-c', script]))
        return {**result, 'folder': job['folder'], 'workdir': job['workdir']} if result else None

    def status(self, job):
        if not isinstance(job, dict) or not job.get('folder'): raise ValueError('Status requires the task handle returned by start')
        script = "import pathlib,json,subprocess; folder=pathlib.Path(" + repr(job['folder']) + "); p=folder/'state.json'; s=json.loads(p.read_text()); "
        script += "\nif s['status'] in ('running','paused'):\n actual=subprocess.run(['ps','-p',str(s['pid']),'-o','lstart='],capture_output=True,text=True).stdout.strip()\n if actual!=s['process_created']: s.update(status='lost',error='Remote process ended without receipt')\n elif (folder/'pause.json').exists(): s['status']='paused'\n else: s['status']='running'\nprint(json.dumps(s))"
        return {**json.loads(self.command(['python3', '-c', script])), 'folder': job['folder']}

    def control(self, job, action):
        if action not in ('pause', 'resume'): raise ValueError('Use pause or resume')
        script = 'FOLDER='+repr(job['folder'])+'\nTASK='+repr(job['task_id'])+'\nACTION='+repr(action)+'\n'+r'''import pathlib,json,subprocess,os,signal,time,fcntl
folder=pathlib.Path(FOLDER); folder.mkdir(parents=True,exist_ok=True)
lock=(folder/'launch.lock').open('a'); fcntl.flock(lock,fcntl.LOCK_EX)
if (folder/'cancel.json').exists():
 print((folder/'cancel.json').read_text()); raise SystemExit
marker=folder/'pause.json'; state=folder/'state.json'
if ACTION=='pause':
 temporary=folder/'pause.tmp'; temporary.write_text(json.dumps({'task_id':TASK,'accepted_at':time.time()})); temporary.replace(marker)
else: marker.unlink(missing_ok=True)
if not state.exists():
 print(json.dumps({'task_id':TASK,'status':'paused' if ACTION=='pause' else 'starting','observation':'durable launch barrier','control_confirmed':True})); raise SystemExit
s=json.loads(state.read_text())
if s.get('task_id')!=TASK: raise ValueError('Remote task identity mismatch')
if s['status'] in ('completed','failed','cancelled','lost'):
 print(json.dumps(s)); raise SystemExit
actual=subprocess.run(['ps','-p',str(s['pid']),'-o','lstart='],capture_output=True,text=True).stdout.strip()
if actual!=s.get('process_created'): raise RuntimeError('Remote executor identity was lost')
# A pre-launch supervisor polls the durable marker without holding the lock.
# Only a supervisor that has released its launch lock may have a live command.
if s.get('command_pid'):
 os.killpg(s['pid'],signal.SIGSTOP if ACTION=='pause' else signal.SIGCONT)
 def group_states():
  rows=subprocess.run(['ps','-eo','pid=,pgid=,stat='],capture_output=True,text=True,check=True).stdout.splitlines()
  return [line.split()[2] for line in rows if len(line.split())>=3 and line.split()[1]==str(s['pid']) and not line.split()[2].startswith('Z')]
 for _ in range(60):
  states=group_states()
  if states and (all(status.startswith('T') for status in states) if ACTION=='pause' else all(not status.startswith('T') for status in states)): break
  time.sleep(.05)
 else: raise RuntimeError('Remote process group control remains unconfirmed')
s.update(status='paused' if ACTION=='pause' else 'running',control_confirmed=True,observed_at=time.time())
temporary=folder/'control.tmp'; temporary.write_text(json.dumps(s)); temporary.replace(state)
print(json.dumps(s))
'''
        return {**json.loads(self.command(['python3', '-c', script])), 'folder': job['folder']}

    def output(self, job, offset=0, limit=100000):
        script = "import pathlib,json; p=pathlib.Path(" + repr(job['folder']) + ")/'stdout.txt'; f=p.open('rb'); f.seek(" + str(max(0, int(offset))) + "); data=f.read(" + str(min(max(1, int(limit)), 1_000_000)) + "); print(json.dumps({'text':data.decode(errors='replace'),'offset':f.tell()}))"
        return json.loads(self.command(['python3', '-c', script]))

    def observe(self, job, cursor=''):
        # The executor already owns this exact remote workspace. No UI-triggered SSH.
        script = 'ROOT='+repr(job['workdir'])+'\nCURSOR='+repr(cursor)+'\n'+r'''import os,json,pathlib
root=pathlib.Path(ROOT).resolve(); page=[]; more=False; budget=0
excluded={'node_modules','__pycache__','agent_session.json','secrets.json','credentials.json','model_context','planning_sessions','model_responses','venv'}
# Sorted depth-first order and a cursor in that order; no full-tree materialization.
found=not CURSOR
for directory,dirs,files in os.walk(root,followlinks=False):
 dirs[:]=sorted(v for v in dirs if not v.startswith('.') and v not in excluded and not (pathlib.Path(directory)/v).is_symlink())
 for name in sorted(files):
  p=pathlib.Path(directory)/name; relative=p.relative_to(root).as_posix()
  if name.startswith('.') or name in excluded or p.is_symlink() or p.suffix in ('.pem','.key','.p12','.pfx'): continue
  if not found:
   if relative==CURSOR: found=True
   continue
  if len(page)>=200: more=True; break
  try:
   st=p.stat(); item={'path':relative,'size':st.st_size,'mtime_ns':st.st_mtime_ns}
   if st.st_size<=32768 and budget+st.st_size<=524288:
    before=p.read_bytes(); after=p.read_bytes(); end=p.stat()
    if before==after and (st.st_ino,st.st_size,st.st_mtime_ns)==(end.st_ino,end.st_size,end.st_mtime_ns):
     try: item['content']=before.decode('utf-8'); budget+=len(before)
     except UnicodeDecodeError: pass
   page.append(item)
  except OSError: continue
 if more: break
print(json.dumps({'files':page,'cursor':CURSOR,'has_more':more,'next_cursor':page[-1]['path'] if more and page else None,'cursor_found':found}))
'''
        return json.loads(self.command(['python3','-c',script]))

    def stop(self, job):
        script = 'FOLDER='+repr(job['folder'])+'\nTASK='+repr(job['task_id'])+'\n'+r'''import pathlib,json,subprocess,os,signal,time,fcntl
folder=pathlib.Path(FOLDER); folder.mkdir(parents=True,exist_ok=True)
lock=(folder/'launch.lock').open('a'); fcntl.flock(lock,fcntl.LOCK_EX)
marker={'task_id':TASK,'status':'cancelled','finished_at':time.time(),'stop_confirmed':False}
temporary=folder/'cancel.tmp'; temporary.write_text(json.dumps(marker)); temporary.replace(folder/'cancel.json')
state=folder/'state.json'
if not state.exists():
 marker.update(stop_confirmed=True,observation='launch prevented by durable cancellation marker')
else:
 s=json.loads(state.read_text())
 if s.get('task_id')!=TASK: raise ValueError('Remote task identity mismatch')
 actual=subprocess.run(['ps','-p',str(s['pid']),'-o','lstart='],capture_output=True,text=True).stdout.strip()
 if actual==s.get('process_created'):
  try:
   os.killpg(s['pid'],signal.SIGTERM); os.killpg(s['pid'],signal.SIGCONT)
  except ProcessLookupError: pass
  time.sleep(.2)
  try: os.killpg(s['pid'],signal.SIGKILL)
  except ProcessLookupError: pass
 def live_group():
  rows=subprocess.run(['ps','-eo','pid=,pgid=,stat='],capture_output=True,text=True,check=True).stdout.splitlines()
  return [line for line in rows if len(line.split())>=3 and line.split()[1]==str(s['pid']) and not line.split()[2].startswith('Z')]
 for _ in range(60):
  if not live_group(): break
  time.sleep(.05)
 if live_group(): raise RuntimeError('Remote process group stop remains unconfirmed')
 marker.update(stop_confirmed=True,pid=s['pid'],process_created=s.get('process_created'))
temporary=folder/'cancel.tmp'; temporary.write_text(json.dumps(marker)); temporary.replace(folder/'cancel.json')
print(json.dumps(marker))
'''
        return {**json.loads(self.command(['python3', '-c', script])), 'folder': job['folder']}

    def fetch(self, remote_path, local_path):
        opts = ['-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=yes', '-P', str(int(self.config.get('port', 22)))]
        if self.config.get('known_hosts_file'): opts += ['-o', 'UserKnownHostsFile='+self.config['known_hosts_file']]
        if self.config.get('identity_file'): opts += ['-i', self.config['identity_file']]
        subprocess.run(['scp', *opts, f'{self.target}:{remote_path}', str(local_path)], check=True, timeout=120)

    def upload_workspace(self, local_path, remote_path):
        """Stream a real workspace archive through SSH; no shell interpolation."""
        import tarfile
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryFile() as archive:
            with tarfile.open(fileobj=archive, mode='w') as bundle:
                for path in Path(local_path).iterdir():
                    if path.name not in ('.forest-processes', '.forest-tasks'):
                        bundle.add(path, arcname=path.name, recursive=True)
            archive.seek(0)
            code = "import pathlib,tarfile,sys; root=pathlib.Path(" + repr(remote_path) + "); root.mkdir(parents=True,exist_ok=True); archive=tarfile.open(fileobj=sys.stdin.buffer,mode='r|'); "
            code += "\nfor member in archive:\n target=(root/member.name).resolve()\n if not target.is_relative_to(root.resolve()) or member.issym() or member.islnk(): raise ValueError('Unsafe archive member')\n archive.extract(member,path=root)"
            result = subprocess.run(['ssh', *self.options, self.target, shlex.join(['python3', '-c', code])], stdin=archive, capture_output=True)
            if result.returncode: raise RuntimeError(result.stderr.decode(errors='replace')[-4000:])


def execute_remote(config, workspace, output, env=None, require_metrics=False):
    """Run or reconnect to a real SSH job and fetch declared output artifacts.

    config.remote contains SSH configuration, absolute workdir and outputs.
    Handles survive local executor restarts. Remote SSH interruptions raise a
    real error; recovery.mode=restart reconnects to the same remote process.
    """
    import os
    import time
    from pathlib import Path
    remote = config.get('remote')
    if not remote: raise ValueError('Configure remote SSH settings before remote execution')
    workdir = remote.get('workdir')
    if not workdir or not workdir.startswith('/'): raise ValueError('Remote workdir must be an absolute path')
    runner = RemoteRunner(remote)
    output = Path(output); workspace = Path(workspace)
    handle_path = output / 'remote_task.json'
    if handle_path.exists():
        job = json.loads(handle_path.read_text())
        if job.get('status') == 'launch_intent':
            resolved = runner.resolve_launch(job)
            if resolved:
                job = resolved
                temporary = handle_path.with_suffix('.tmp'); temporary.write_text(json.dumps(job, indent=2)); temporary.replace(handle_path)
            else:
                handle_path.unlink()
                return execute_remote(config, workspace, output, env=env, require_metrics=require_metrics)
    else:
        # Each run gets its own editable directory; parallel jobs do not overwrite it.
        workdir = workdir.rstrip('/') + '/' + os.environ.get('FOREST_RUN_ID', str(uuid.uuid4()))
        task_id = os.environ.get('FOREST_RUN_ID') or str(uuid.uuid4())
        # Persist identity before SSH upload/start. A lost response can be
        # reconciled, and cancellation can prohibit a late remote launch.
        intent = {'task_id': task_id, 'folder': workdir+'/.forest-tasks/'+task_id,
                  'workdir': workdir, 'status': 'launch_intent'}
        temporary = handle_path.with_suffix('.tmp'); temporary.write_text(json.dumps(intent, indent=2)); temporary.replace(handle_path)
        runner.upload_workspace(workspace, workdir)
        command = config.get('command')
        if not command: raise ValueError('Remote execution requires a command')
        argv = ['/bin/sh', '-c', command] if isinstance(command, str) else command
        forwarded = {'FOREST_RUN_ID': os.environ.get('FOREST_RUN_ID', ''), 'FOREST_ATTEMPT_ID': os.environ.get('FOREST_ATTEMPT_ID', ''), 'FOREST_RUN_DIR': workdir}
        # Remote-specific settings override task defaults, but execution identity
        # and the remote run directory always belong to the worker/adapter.
        task_env = {**(config.get('env') or {}), **remote.get('env', {}), **forwarded}
        job = runner.start(argv, workdir, task_id=task_id, env=task_env, timeout=config.get('timeout'))
        job['workdir'] = workdir
        temporary = handle_path.with_suffix('.tmp'); temporary.write_text(json.dumps(job, indent=2)); temporary.replace(handle_path)
    if job.get('status') == 'cancelled': raise RuntimeError('Remote launch was cancelled before execution')
    offset = 0
    offset_path = output / 'remote_output_offset.json'
    if offset_path.exists(): offset = int(json.loads(offset_path.read_text()).get('offset', 0))
    manifest_cursor=''; last_observation=0
    while True:
        try:
            status = runner.status(job)
        except Exception:
            from services.observation.remote import capture
            capture(os.environ.get('FOREST_RUN_ID'),None,os.environ.get('FOREST_ATTEMPT_ID'))
            raise
        if time.monotonic()-last_observation>=3 or status['status'] in ('completed','failed','cancelled','lost'):
            from services.observation.remote import capture
            try:
                observed=runner.observe(job,manifest_cursor)
                capture(os.environ.get('FOREST_RUN_ID'),observed,os.environ.get('FOREST_ATTEMPT_ID'))
                manifest_cursor=observed.get('next_cursor') or ''
            except Exception:
                capture(os.environ.get('FOREST_RUN_ID'),None,os.environ.get('FOREST_ATTEMPT_ID'))
            last_observation=time.monotonic()
        chunk = runner.output(job, offset)
        if chunk['text']: print(chunk['text'], end='', flush=True)
        offset = chunk['offset']; offset_path.write_text(json.dumps({'offset': offset}))
        if status['status'] in ('completed', 'failed', 'cancelled', 'lost'): break
        time.sleep(1)
    (output / 'remote_result.json').write_text(json.dumps(status, indent=2))
    outputs = list(dict.fromkeys(([config.get('metrics_file', 'metrics.json')] if require_metrics else []) + remote.get('outputs', [])))
    for relative in outputs:
        if Path(relative).is_absolute() or '..' in Path(relative).parts: raise ValueError('Output paths must be relative to the remote workspace')
        target = workspace / relative; target.parent.mkdir(parents=True, exist_ok=True)
        try: runner.fetch(job['workdir'] + '/' + relative, target)
        except (subprocess.SubprocessError,RuntimeError):
            if status['status']=='completed': raise
    if status['status'] != 'completed': raise RuntimeError('Remote task '+status['status']+': '+str(status.get('error',status.get('exit_code'))))
    metrics_path = workspace / config.get('metrics_file', 'metrics.json')
    metrics = json.loads(metrics_path.read_text()) if metrics_path.is_file() else {}
    return {**metrics, 'command_exit_code': status.get('exit_code'), 'remote_execution': status}


def cancel_remote(config, output, *, run_id=None):
    from pathlib import Path
    path = Path(output) / 'remote_task.json'
    if config.get('remote') and path.exists():
        return RemoteRunner(config['remote']).stop(json.loads(path.read_text()))
    if config.get('remote') and run_id:
        workdir=config['remote']['workdir'].rstrip('/')+'/'+run_id
        return RemoteRunner(config['remote']).stop({'task_id':run_id, 'folder':workdir+'/.forest-tasks/'+run_id})


def control_remote(config, output, action, *, run_id=None):
    from pathlib import Path
    path = Path(output) / 'remote_task.json'
    if not config.get('remote'): return None
    if path.exists(): job = json.loads(path.read_text())
    elif run_id:
        workdir = config['remote']['workdir'].rstrip('/')+'/'+run_id
        job = {'task_id': run_id, 'folder': workdir+'/.forest-tasks/'+run_id}
    else: return None
    result = RemoteRunner(config['remote']).control(job, action)
    if action == 'resume' and result['status'] in ('cancelled', 'lost'):
        raise RuntimeError('Remote task cannot resume after cancellation or identity loss')
    return result
