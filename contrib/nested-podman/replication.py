#!/usr/bin/env python3
"""Bounded, disposable checkpoint replication experiment. No automatic HA."""
import argparse
import hashlib
import json
from pathlib import Path
import time
import uuid
from lab import ssh, node

SNAPSHOT = '''
import sys,json,hashlib,time
sys.path.insert(0,'/opt/vzcriu-kit');import node as n
name,job=sys.argv[1:]; before=n.proof(name)
folder=n.BASE/job;folder.mkdir(mode=0o700)
(folder/'before.json').write_text(json.dumps(before))
n.pod('container','checkpoint',name,'--leave-running','--file-locks','--keep','--export='+str(folder/'checkpoint.tar'))
a=n.proof(name);time.sleep(2);b=n.proof(name)
assert b['outer_running'] and b['counter']>a['counter'] and b['uuid']==before['uuid']
print(json.dumps({'before':before,'continued':b,'sha256':hashlib.sha256((folder/'checkpoint.tar').read_bytes()).hexdigest()}))
'''
STOP = '''
import sys,json
sys.path.insert(0,'/opt/vzcriu-kit');import node as n
name=sys.argv[1]; n.owned(name);n.pod('kill','--signal=KILL',name)
assert not n.owned(name)['State']['Running']
print(json.dumps({'source_stopped':True}))
'''

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source',required=True);p.add_argument('--target',required=True)
    p.add_argument('--evidence',required=True)
    o=p.parse_args()
    ident=uuid.uuid4().hex;name='replication-'+ident[:12]
    out=Path(o.evidence)/name;out.mkdir(parents=True,mode=0o700)
    report={'name':name,'status':'STARTED','checkpoints':[], 'scope':'network-free nested disposable workload; no volumes or external effects'}
    def save(): (out/'result.json').write_text(json.dumps(report,indent=2))
    save()
    try:
        src=node(o.source,'identity',name);dst=node(o.target,'preflight',name)
        assert src['machine_id']!=dst['machine_id'] and src['compatibility']==dst['compatibility']
        report['initial']=node(o.source,'create',name);save()
        for i in range(3):
            job=f'replica-{ident}-{i}';start=time.monotonic()
            snap=json.loads(ssh(o.source,['sudo','python3','-c',SNAPSHOT,name,job]))
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
        report['failure']=json.loads(ssh(o.source,['sudo','python3','-c',STOP,name]));save()
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
