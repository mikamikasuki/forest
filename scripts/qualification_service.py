#!/usr/bin/env python3
"""Persist compute/soak monitors on macOS without resubmitting compute work.

Foreground operation also works on Linux. A successful or failed completed
qualification exits normally; only a crashed supervisor is restarted by launchd.
"""
from __future__ import annotations
import argparse
import fcntl
import json
import os
from pathlib import Path
import platform
import plistlib
import signal
import subprocess
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from scripts.qualify_release import report_lock, save, utc

LABEL='org.forest.qualification'
FINISHED={'passed','failed'}


def read_report(path):
    path=Path(path)
    return json.loads(path.read_text()) if path.exists() else {}


def monitor_owner(path):
    """Query an OS lock, not merely the existence of a stale PID file."""
    lock_path=Path(str(path)+'.monitor.lock')
    if not lock_path.exists(): return None
    with lock_path.open('a+') as handle:
        try: fcntl.flock(handle.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:
            handle.seek(0)
            try: return json.load(handle)
            except (ValueError,OSError): return {'pid':None}
        else:
            fcntl.flock(handle.fileno(),fcntl.LOCK_UN); return None


def specs(args):
    compute=read_report(args.compute_report)
    if not compute.get('run_id') or compute.get('mode')!='compute':
        raise ValueError('Compute report must identify an already submitted compute run; this service never submits one.')
    if compute.get('url')!=args.url: raise ValueError('Compute report URL differs from --url')
    return {
        'compute':{'report':Path(args.compute_report).resolve(),'seconds':float(compute['requested_seconds'])},
        'soak':{'report':Path(args.soak_report).resolve(),'seconds':float(args.soak_seconds)},
    }


def command(args,mode,spec):
    return [str(ROOT/'.venv/bin/python'),str(ROOT/'scripts/qualify_release.py'),mode,'--url',args.url,'--report',str(spec['report']),'--resume','--seconds',str(spec['seconds']),'--poll',str(args.poll),'--interval',str(args.interval)]


def supervise(args):
    monitors=specs(args); directory=Path(args.log_dir).resolve(); directory.mkdir(parents=True,exist_ok=True)
    state_path=directory/'status.json'; state={'status':'running','pid':os.getpid(),'started_at':utc(),'url':args.url,'monitors':{},'events':[],'restart_policy':'Retry interrupted/error monitors after the configured delay; never rerun a finished report or submit a new compute job.'}
    children={}; handles={}; retry_at={}; stopping=False
    def stop(*_):
        nonlocal stopping
        stopping=True
    old_signals={s:signal.getsignal(s) for s in (signal.SIGTERM,signal.SIGINT)}
    signal.signal(signal.SIGTERM,stop); signal.signal(signal.SIGINT,stop)
    def event(kind,**details):
        row={'at':utc(),'event':kind,**details}; state['events'].append(row); state['events']=state['events'][-200:]; print(json.dumps(row),flush=True)
    try:
        with report_lock(directory/'supervisor'):
            event('supervisor_started',pid=os.getpid())
            while not stopping:
                all_finished=True
                for mode,spec in monitors.items():
                    report=read_report(spec['report']); proc=children.get(mode)
                    info=state['monitors'].setdefault(mode,{'report':str(spec['report'])})
                    info.update(report_status=report.get('status','not_started'),run_id=report.get('run_id'),project_id=report.get('project_id'),observed_seconds=report.get('observed_compute_seconds',report.get('observed_seconds')),updated_at=report.get('updated_at'))
                    if proc is not None and proc.poll() is not None:
                        event('monitor_exited',mode=mode,pid=proc.pid,exit_code=proc.returncode,report_status=report.get('status'))
                        children.pop(mode); handles.pop(mode).close(); info['last_exit_code']=proc.returncode; proc=None; retry_at[mode]=time.monotonic()+args.retry_delay
                    owner=monitor_owner(spec['report'])
                    info['pid']=proc.pid if proc is not None else (owner or {}).get('pid')
                    info['supervised_child']=proc is not None
                    if report.get('status') in FINISHED:
                        info['status']='finished'; continue
                    all_finished=False
                    if owner or proc is not None:
                        info['status']='monitoring'; continue
                    if time.monotonic()<retry_at.get(mode,0):
                        info['status']='waiting_to_retry'; continue
                    # Recheck original compute identity immediately before every
                    # child launch. Missing reports are not permission to create work.
                    if mode=='compute' and not report.get('run_id'):
                        raise ValueError('Existing compute run identity disappeared; stopped without submitting anything.')
                    handle=(directory/(mode+'.log')).open('ab'); handles[mode]=handle
                    proc=subprocess.Popen(command(args,mode,spec),cwd=ROOT,stdout=handle,stderr=subprocess.STDOUT,start_new_session=True)
                    children[mode]=proc; info.update(pid=proc.pid,status='starting',supervised_child=True)
                    event('monitor_started',mode=mode,pid=proc.pid,run_id=report.get('run_id'))
                state['updated_at']=utc(); save(state_path,state)
                if all_finished:
                    state.update(status='finished',finished_at=utc()); save(state_path,state); event('all_reports_finished'); return 0
                time.sleep(min(args.supervisor_poll,5))
    except (ValueError,OSError,RuntimeError) as exc:
        # A permanent configuration error exits cleanly so launchd does not
        # repeatedly execute an always-failing configuration.
        if isinstance(exc,RuntimeError) and str(exc).startswith('Another monitor owns'):
            print(str(exc),flush=True); return 0
        state.update(status='configuration_error',error=str(exc),finished_at=utc()); save(state_path,state); event('configuration_error',error=str(exc)); return 0
    finally:
        for proc in children.values():
            if proc.poll() is None: proc.terminate()
        for proc in children.values():
            try: proc.wait(timeout=15)
            except subprocess.TimeoutExpired:
                proc.kill(); proc.wait(timeout=5)
        for handle in handles.values(): handle.close()
        for sig,handler in old_signals.items(): signal.signal(sig,handler)
        if stopping:
            state.update(status='monitoring_stopped',finished_at=utc(),note='Server tasks were not cancelled; monitor reports can resume.'); save(state_path,state)
    return 0


def launchd_definition(args):
    directory=Path(args.log_dir).resolve(); directory.mkdir(parents=True,exist_ok=True)
    argv=[str(ROOT/'.venv/bin/python'),str(ROOT/'scripts/qualification_service.py'),'run','--url',args.url,'--compute-report',str(Path(args.compute_report).resolve()),'--soak-report',str(Path(args.soak_report).resolve()),'--soak-seconds',str(args.soak_seconds),'--log-dir',str(directory),'--interval',str(args.interval),'--poll',str(args.poll),'--retry-delay',str(args.retry_delay)]
    return {'Label':LABEL,'ProgramArguments':argv,'WorkingDirectory':str(ROOT),'RunAtLoad':True,'KeepAlive':{'SuccessfulExit':False},'ThrottleInterval':60,'StandardOutPath':str(directory/'supervisor.stdout.log'),'StandardErrorPath':str(directory/'supervisor.stderr.log'),'EnvironmentVariables':{'PYTHONUNBUFFERED':'1'}}


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__); parser.add_argument('action',choices=['install','render','status','remove','run'])
    parser.add_argument('--url',default='http://127.0.0.1:8000'); parser.add_argument('--compute-report',type=Path,default=ROOT/'var/qa/release-compute.json'); parser.add_argument('--soak-report',type=Path,default=ROOT/'var/qa/release-soak.json')
    parser.add_argument('--soak-seconds',type=float,default=86400); parser.add_argument('--interval',type=float,default=60); parser.add_argument('--poll',type=float,default=2); parser.add_argument('--retry-delay',type=float,default=60); parser.add_argument('--supervisor-poll',type=float,default=5); parser.add_argument('--log-dir',type=Path,default=ROOT/'var/qa/qualification-service')
    args=parser.parse_args(argv)
    if min(args.soak_seconds,args.interval,args.poll,args.retry_delay,args.supervisor_poll)<=0: parser.error('Durations must be positive')
    if args.action=='run':
        try: return supervise(args)
        except (ValueError,OSError) as exc:
            print(json.dumps({'status':'configuration_error','error':str(exc),'at':utc()}),flush=True)
            return 0
    if platform.system()!='Darwin': parser.error('Use the foreground run action on Linux; launchd installation is macOS-only')
    target=Path.home()/'Library/LaunchAgents'/f'{LABEL}.plist'; domain=f'gui/{os.getuid()}'; service=f'{domain}/{LABEL}'
    if args.action=='status':
        result=subprocess.run(['launchctl','print',service],capture_output=True,text=True); print(result.stdout or result.stderr)
        status=Path(args.log_dir)/'status.json'
        if status.exists(): print(status.read_text())
        return result.returncode
    if args.action=='remove':
        subprocess.run(['launchctl','bootout',domain,str(target)],check=True); target.unlink(missing_ok=True); print('Qualification monitors removed; server tasks and reports retained.'); return 0
    specs(args)
    payload=plistlib.dumps(launchd_definition(args)); rendered=Path(args.log_dir)/f'{LABEL}.plist'; rendered.write_bytes(payload)
    if args.action=='render': print(rendered); return 0
    loaded=subprocess.run(['launchctl','print',service],capture_output=True).returncode==0
    if loaded:
        if not target.exists() or target.read_bytes()!=payload:
            raise SystemExit('A qualification service is already loaded with different settings. Remove its monitor service before explicitly installing new settings; server tasks are preserved.')
        print('Qualification monitor service is already installed; no duplicate monitor was started.'); return 0
    target.parent.mkdir(parents=True,exist_ok=True); temporary=target.with_suffix('.tmp'); temporary.write_bytes(payload); temporary.replace(target)
    subprocess.run(['launchctl','bootstrap',domain,str(target)],check=True); print('Installed persistent qualification monitors: '+str(target)); return 0

if __name__=='__main__': raise SystemExit(main())
