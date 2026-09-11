#!/usr/bin/env python3
"""Bounded, disposable checkpoint replication experiment. No automatic HA."""
import argparse
import hashlib
import json
from pathlib import Path
import time
import uuid
from lab import ssh, node, resolved_mode, kit_lib_dir

# Both snippets take the node.py import directory (sys.argv[1]) AND the
# resolved runtime mode (sys.argv[2], "manual" or "packaged") as leading
# positional arguments, not environment variables: they run as
# 'sudo python3 -c <snippet> <lib_dir> <mode> <name> [job]' over ssh, and
# sudo on the target host typically resets/filters the environment (no -E,
# no env_keep assumed), so anything communicated through os.environ set on
# the controller side would not reliably reach the remote interpreter.
# Baking both the controller-resolved directory AND mode into the command
# line, the same way `name`/`job` already are, survives that unconditionally.
#
# These are two SEPARATE decisions and both must be forced explicitly:
#   1. WHICH node.py FILE gets imported (argv[1] -> sys.path.insert), and
#   2. WHAT RUNTIME node.py ITSELF selects once imported, i.e. the CRIU
#      binary/wrapper it calls (node.py's own _detect_runtime(), driven by
#      its PODMESH_VZCRIU_RUNTIME env var read at import time).
# Fixing only (1) is not enough: node.py's module-level `RUNTIME =
# _detect_runtime()` runs the instant `import node` executes, using
# whatever PODMESH_VZCRIU_RUNTIME happens to be in THIS remote process's
# environment -- which sudo will normally have stripped, so it would fall
# back to node.py's own autodetection (manual-preferred) regardless of what
# the controller resolved. So argv[2] is used to set
# os.environ['PODMESH_VZCRIU_RUNTIME'] in the remote interpreter BEFORE
# `import node`, forcing node.py's own mode to match, deterministically, on
# a host where both a manual and a packaged kit/runtime happen to coexist.
SNAPSHOT = '''
import sys,os,json,hashlib,time
sys.path.insert(0,sys.argv[1])
os.environ['PODMESH_VZCRIU_RUNTIME']=sys.argv[2]
import node as n
name,job=sys.argv[3:]; before=n.proof(name)
folder=n.BASE/job;folder.mkdir(mode=0o700)
(folder/'before.json').write_text(json.dumps(before))
n.pod('container','checkpoint',name,'--leave-running','--file-locks','--keep','--export='+str(folder/'checkpoint.tar'))
a=n.proof(name);time.sleep(2);b=n.proof(name)
assert b['outer_running'] and b['counter']>a['counter'] and b['uuid']==before['uuid']
print(json.dumps({'before':before,'continued':b,'sha256':hashlib.sha256((folder/'checkpoint.tar').read_bytes()).hexdigest()}))
'''
STOP = '''
import sys,os,json
sys.path.insert(0,sys.argv[1])
os.environ['PODMESH_VZCRIU_RUNTIME']=sys.argv[2]
import node as n
name=sys.argv[3]; n.owned(name);n.pod('kill','--signal=KILL',name)
assert not n.owned(name)['State']['Running']
print(json.dumps({'source_stopped':True}))
'''

def snapshot_argv(name, job, lib_dir=None, mode=None):
    mode = mode or resolved_mode()
    return ['sudo', 'python3', '-c', SNAPSHOT, lib_dir or kit_lib_dir(mode), mode, name, job]

def stop_argv(name, lib_dir=None, mode=None):
    mode = mode or resolved_mode()
    return ['sudo', 'python3', '-c', STOP, lib_dir or kit_lib_dir(mode), mode, name]

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source',required=True);p.add_argument('--target',required=True)
    p.add_argument('--evidence',required=True)
    o=p.parse_args()
    ident=uuid.uuid4().hex;name='replication-'+ident[:12]
    out=Path(o.evidence)/name;out.mkdir(parents=True,mode=0o700)
    report={'name':name,'status':'STARTED','checkpoints':[], 'mode':resolved_mode(), 'scope':'network-free nested disposable workload; no volumes or external effects'}
    def save(): (out/'result.json').write_text(json.dumps(report,indent=2))
    save()
    try:
        src=node(o.source,'identity',name);dst=node(o.target,'preflight',name)
        assert src['machine_id']!=dst['machine_id'] and src['compatibility']==dst['compatibility']
        report['initial']=node(o.source,'create',name);save()
        for i in range(3):
            job=f'replica-{ident}-{i}';start=time.monotonic()
            snap=json.loads(ssh(o.source,snapshot_argv(name,job)))
            remote='/var/lib/vzcriu-kit/'+job
            archive=ssh(o.source,['sudo','cat',remote+'/checkpoint.tar'])
            assert hashlib.sha256(archive).hexdigest()==snap['sha256']
            ssh(o.target,['sudo','mkdir','-m','700',remote])
            ssh(o.target,['sudo','tee',remote+'/checkpoint.tar'],archive)
            ssh(o.target,['sudo','tee',remote+'/before.json'],json.dumps(snap['before']).encode())
            assert ssh(o.target,['sudo','sha256sum',remote+'/checkpoint.tar']).decode().split()[0]==snap['sha256']
            report['checkpoints'].append(dict(snap,job=job,bytes=len(archive),seconds=time.monotonic()-start));save()
            print('Verified replica',i+1,flush=True)
            time.sleep(5)
        report['before_failure']=node(o.source,'proof',name);save()
        report['failure']=json.loads(ssh(o.source,stop_argv(name)));save()
        assert not node(o.source,'state',name)['running']
        start=time.monotonic();report['restored']=node(o.target,'restore',name,job)
        report['restore_seconds']=time.monotonic()-start
        report['status']='PASSED'
    except Exception as e:
        report['status']='FAILED';report['error']=str(e)
        if getattr(e,'stderr',None):report['stderr']=e.stderr.decode(errors='replace')
        raise
    finally:save()
    print(json.dumps(report,indent=2))
if __name__=='__main__':main()
