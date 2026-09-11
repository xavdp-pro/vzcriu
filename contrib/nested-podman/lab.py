#!/usr/bin/env python3
"""Controller for disposable, network-free nested Podman migration experiments."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import time
import uuid

HERE = Path(__file__).resolve().parent

# Controller-side mode: which remote node-host layout lab.py/replication.py
# should talk to. Two values are meaningful:
#  - "manual" (default when nothing is set): unchanged original behavior,
#    talks to a hand-installed/source-tree kit at /opt/vzcriu-kit on the node.
#  - "packaged": talks to a node host with podmesh-vzcriu-helpers-node
#    installed (command podmesh-vzcriu-node, scripts under
#    /opt/podmesh-vzcriu-kit/lib).
# The installed podmesh-vzcriu-lab/podmesh-vzcriu-replication wrapper
# commands set PODMESH_VZCRIU_RUNTIME=packaged by default (only if the caller
# hasn't already set it) before dispatching here, so a clean packaged install
# works correctly with no environment variables required from the user.
# Running lab.py/replication.py directly from a checkout leaves this unset
# and keeps the original manual default. PODMESH_VZCRIU_NODE_CMD remains an
# explicit override of the resulting remote command, independent of mode.
DEFAULT_NODE_CMD_BY_MODE = {
    'manual': 'sudo python3 /opt/vzcriu-kit/node.py',
    'packaged': 'sudo podmesh-vzcriu-node',
}
# Where replication.py's remote inline snippets should import node.py from,
# for each mode. Kept alongside DEFAULT_NODE_CMD_BY_MODE since both are
# derived from the same resolved mode. Each has a matching env var override
# (mainly for tests: it lets a test point "manual"/"packaged" at a temp
# directory instead of requiring real content at /opt/vzcriu-kit or
# /opt/podmesh-vzcriu-kit/lib).
KIT_LIB_DIR_BY_MODE = {
    'manual': '/opt/vzcriu-kit',
    'packaged': '/opt/podmesh-vzcriu-kit/lib',
}
KIT_LIB_DIR_ENV_BY_MODE = {
    'manual': 'PODMESH_VZCRIU_MANUAL_KIT_LIB',
    'packaged': 'PODMESH_VZCRIU_PACKAGED_KIT_LIB',
}

def resolved_mode():
    mode = os.environ.get('PODMESH_VZCRIU_RUNTIME', '').strip().lower()
    if not mode:
        return 'manual'
    if mode not in DEFAULT_NODE_CMD_BY_MODE:
        raise ValueError(f'Unknown PODMESH_VZCRIU_RUNTIME {mode!r}; expected "manual" or "packaged"')
    return mode

def kit_lib_dir(mode=None):
    mode = mode or resolved_mode()
    return os.environ.get(KIT_LIB_DIR_ENV_BY_MODE[mode], KIT_LIB_DIR_BY_MODE[mode])

def ssh(host, argv, data=None):
    if not re.fullmatch(r'[A-Za-z0-9_.@-]+', host) or host.startswith('-'):
        raise ValueError('Invalid SSH host')
    return subprocess.run(['ssh', '-oBatchMode=yes', '-oConnectTimeout=10', host, shlex.join(argv)], input=data, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True, timeout=180).stdout

def node_command():
    override = os.environ.get('PODMESH_VZCRIU_NODE_CMD')
    if override:
        return shlex.split(override)
    return shlex.split(DEFAULT_NODE_CMD_BY_MODE[resolved_mode()])

def remote_node_argv(action, name, job=''):
    args = node_command() + [action, '--name', name]
    if job: args += ['--job', job]
    return args

def node(host, action, name, job=''):
    return json.loads(ssh(host, remote_node_argv(action, name, job)))

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
