#!/usr/bin/env python3
"""Controller for disposable, network-free nested Podman migration experiments."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import shlex
import subprocess
import time
import uuid

HERE = Path(__file__).resolve().parent

def ssh(host, argv, data=None):
    if not re.fullmatch(r'[A-Za-z0-9_.@-]+', host) or host.startswith('-'):
        raise ValueError('Invalid SSH host')
    return subprocess.run(['ssh', '-oBatchMode=yes', '-oConnectTimeout=10', host, shlex.join(argv)], input=data, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True, timeout=180).stdout

def node(host, action, name, job=''):
    args = ['sudo', 'python3', '/opt/vzcriu-kit/node.py', action, '--name', name]
    if job: args += ['--job', job]
    return json.loads(ssh(host, args))

def install(host, binary):
    ssh(host, ['sudo', 'mkdir', '-p', '/opt/vzcriu-kit/bin'])
    for dest, content in [('criu.real', Path(binary).read_bytes()), ('node.py', (HERE/'node.py').read_bytes())]:
        ssh(host, ['sudo', 'tee', '/opt/vzcriu-kit/'+dest], content)
    wrapper = b'#!/bin/sh\nexport GLIBC_TUNABLES=glibc.pthread.rseq=0\nexec /opt/vzcriu-kit/criu.real "$@"\n'
    ssh(host, ['sudo','tee','/opt/vzcriu-kit/bin/criu'], wrapper)
    ssh(host, ['sudo','chmod','755','/opt/vzcriu-kit/criu.real','/opt/vzcriu-kit/bin/criu'])
    return {'installed': host, 'binary_sha256': hashlib.sha256(Path(binary).read_bytes()).hexdigest()}

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('action',choices=['install','init','create','proof','migrate','retire'])
    p.add_argument('--source',required=True);p.add_argument('--target')
    p.add_argument('--binary');p.add_argument('--name',default='vzkit-demo')
    p.add_argument('--evidence',default='./vzkit-evidence')
    o=p.parse_args()
    if o.action=='install':
        if not o.binary:p.error('--binary required')
        print(json.dumps(install(o.source,o.binary)));return
    if o.action!='migrate':
        print(json.dumps(node(o.source,o.action,o.name),indent=2));return
    if not o.target or o.target==o.source:p.error('Distinct --target required')
    job='migration-'+uuid.uuid4().hex
    evidence=Path(o.evidence)/job;evidence.mkdir(mode=0o700, parents=True)
    report={'job':job,'status':'STARTED','name':o.name}
    def save(): (evidence/'result.json').write_text(json.dumps(report,indent=2))
    save()
    try:
        target=node(o.target,'preflight',o.name)
        source=node(o.source,'identity',o.name)
        if target['machine_id']==source['machine_id']:raise RuntimeError('Source and target identities match')
        if target['compatibility']!=source['compatibility']:raise RuntimeError('Kernel/runtime/image mismatch')
        report['preflight']=target
        t=time.monotonic()
        report['checkpoint']=node(o.source,'checkpoint',o.name,job);save()
        report['checkpoint_seconds']=time.monotonic()-t
        # Preserve original archive locally; never silently resume source after failure.
        remote='/var/lib/vzcriu-kit/'+job
        archive=ssh(o.source,['sudo','cat',remote+'/checkpoint.tar'])
        if hashlib.sha256(archive).hexdigest()!=report['checkpoint']['sha256']:
            raise RuntimeError('Transfer checksum mismatch')
        (evidence/'checkpoint.tar').write_bytes(archive)
        ssh(o.target,['sudo','mkdir','-m','700',remote])
        ssh(o.target,['sudo','tee',remote+'/checkpoint.tar'],archive)
        metadata=json.dumps(report['checkpoint']['before']).encode()
        ssh(o.target,['sudo','tee',remote+'/before.json'],metadata)
        actual=ssh(o.target,['sudo','sha256sum',remote+'/checkpoint.tar']).decode().split()[0]
        if actual!=report['checkpoint']['sha256']:raise RuntimeError('Target checksum mismatch')
        report['transfer_seconds']=time.monotonic()-t-report['checkpoint_seconds'];save()
        if node(o.source,'state',o.name)['running']:raise RuntimeError('Source resumed unexpectedly')
        report['restore']=node(o.target,'restore',o.name,job)
        report['total_seconds']=time.monotonic()-t
        report['status']='PASSED'
    except Exception as e:
        report['status']='FAILED';report['error']=str(e)
        if isinstance(e,subprocess.CalledProcessError):report['stderr']=e.stderr.decode(errors='replace')
        raise
    finally:save()
    print(json.dumps(report,indent=2))

if __name__=='__main__':main()
