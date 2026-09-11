#!/usr/bin/env python3
"""Root-only, isolated nested-Podman laboratory operations (not production)."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time

BASE = Path('/var/lib/vzcriu-kit')
P = ['podman', '--root=/var/lib/containers/vzkit', '--runroot=/run/containers/vzkit', '--storage-driver=vfs']
INNER = ['podman', '--storage-driver=vfs', '--cgroup-manager=cgroupfs', '--events-backend=file']

# Runtime location. Two layouts are supported:
#  - "manual": the source-tree / hand-installed kit at /opt/vzcriu-kit (original behavior).
#  - "packaged": the podmesh-vzcriu-helpers .deb, which depends on the separately
#    packaged podmesh-vzcriu runtime (/usr/bin/podmesh-vzcriu, /usr/lib/podmesh-vzcriu/criu)
#    and forwards to it through a private-path "criu"-named shim so Podman's own
#    PATH lookup keeps working without touching the distribution criu.
# Each field can be overridden independently (mainly for tests, with a mocked
# binary); PODMESH_VZCRIU_RUNTIME picks a layout explicitly ("manual"/"packaged").
# With no override, the manual kit wins if present (preserves existing behavior),
# otherwise the packaged runtime is used if installed.


def _candidate(env_prefix, wrapper, real, bindir):
    return {
        'wrapper': Path(os.environ.get(env_prefix + '_WRAPPER', wrapper)),
        'real': Path(os.environ.get(env_prefix + '_REAL', real)),
        'bindir': os.environ.get(env_prefix + '_BINDIR', bindir),
    }


def _detect_runtime():
    manual = _candidate('PODMESH_VZCRIU_MANUAL', '/opt/vzcriu-kit/bin/criu', '/opt/vzcriu-kit/criu.real', '/opt/vzcriu-kit/bin')
    packaged = _candidate('PODMESH_VZCRIU_PACKAGED', '/usr/bin/podmesh-vzcriu', '/usr/lib/podmesh-vzcriu/criu', '/opt/podmesh-vzcriu-kit/bin')
    mode = os.environ.get('PODMESH_VZCRIU_RUNTIME', '').strip().lower()
    if mode == 'manual':
        return manual
    if mode == 'packaged':
        return packaged
    if mode:
        raise RuntimeError(f'Unknown PODMESH_VZCRIU_RUNTIME {mode!r}; expected "manual" or "packaged"')
    if manual['real'].is_file():
        return manual
    if packaged['real'].is_file():
        return packaged
    return manual  # unchanged default: error messages still point at the manual kit


RUNTIME = _detect_runtime()
ENV = dict(os.environ, PATH=str(RUNTIME['bindir']) + ':' + os.environ.get('PATH', '/usr/sbin:/usr/bin:/sbin:/bin'), GLIBC_TUNABLES='glibc.pthread.rseq=0')

def run(args, data=None):
    r = subprocess.run(args, input=data, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=ENV, timeout=120)
    if r.returncode:
        raise RuntimeError(f'{args!r}: exit {r.returncode}\n{r.stderr}')
    return r.stdout

def pod(*args):
    return run(P + list(args))

def inside(name, *args):
    return pod('exec', name, *args)

def inspect(name):
    return json.loads(pod('inspect', name))[0]

def owned(name):
    d = inspect(name)
    if d['Config'].get('Labels', {}).get('io.vzcriu-kit.disposable') != 'true':
        raise RuntimeError('Refusing a container not created by this disposable lab kit')
    return d

def init(image):
    BASE.mkdir(mode=0o700, parents=True, exist_ok=True)
    BASE.chmod(0o700)
    # Import explicitly selected image from the normal local store into an isolated store.
    for ref, filename in [(image, 'outer-image.tar'), ('docker.io/library/alpine:3.22', 'inner-image.tar')]:
        run(['podman', 'save', '-o', str(BASE / filename), ref])
        pod('load', '-i', str(BASE / filename))
    return {'initialized': True, 'image': image}

def create(name, image):
    pod('run', '-d', '--name', name, '--label=io.vzcriu-kit.disposable=true', '--privileged', '--network=none', '--log-driver=k8s-file', image, 'sleep', 'infinity')
    pod('cp', str(BASE / 'inner-image.tar'), name + ':/tmp/inner-image.tar')
    inside(name, *INNER, 'load', '-i', '/tmp/inner-image.tar')
    inside(name, *INNER, 'run', '-d', '--name=counter', '--cgroups=disabled', '--cgroupns=host', '--network=none', '--log-driver=k8s-file', 'docker.io/library/alpine:3.22', 'sh', '-c', 'token=$(cat /proc/sys/kernel/random/uuid); n=0; while :; do echo "$(date +%s) $token $n"; n=$((n+1)); sleep 1; done')
    for _ in range(20):
        try:
            return proof(name)
        except (IndexError, ValueError):
            time.sleep(0.25)
    raise RuntimeError('No initial log; partial lab container retained: ' + name)

def proof(name):
    d = owned(name)
    inner = json.loads(inside(name, *INNER, 'inspect', 'counter'))[0]
    lines = inside(name, *INNER, 'logs', '--tail=3', 'counter').strip().splitlines()
    fields = lines[-1].split()
    if len(fields) != 3:
        raise RuntimeError('Unexpected workload output')
    return {'outer_id': d['Id'], 'inner_id': inner['Id'], 'pid': inner['State']['Pid'], 'uuid': fields[1], 'counter': int(fields[2]), 'lines': lines, 'outer_running': d['State']['Running']}

def checkpoint(name, job):
    before = proof(name)
    folder = BASE / job
    folder.mkdir()  # Never overwrite an earlier checkpoint.
    (folder / 'before.json').write_text(json.dumps(before, indent=2))
    pod('container', 'checkpoint', name, '--file-locks', '--keep', '--export=' + str(folder / 'checkpoint.tar'))
    if owned(name)['State']['Running']:
        raise RuntimeError('Source still running after checkpoint')
    return {'before': before, 'sha256': hashlib.sha256((folder / 'checkpoint.tar').read_bytes()).hexdigest(), 'source_stopped': True}

# Run inside the outer container. Validate the preserved process before changing metadata.
RECONCILE = r'''
import json, os, shutil, sys, tempfile
from pathlib import Path
expected=json.loads(sys.argv[1]); ident=expected['inner_id']; pid=expected['pid']
p=Path('/run/crun')/ident/'status'; d=json.loads(p.read_text())
assert d['pid']==pid, 'PID changed unexpectedly'
cmd=Path('/proc/%d/cmdline'%pid).read_bytes()
assert b'token=$(cat /proc/sys/kernel/random/uuid)' in cmd, 'Unexpected process'
log=Path('/var/lib/containers/storage/vfs-containers')/ident/'userdata/ctr.log'
assert expected['uuid'] in log.read_text(), 'Workload identity missing'
start=int(Path('/proc/%d/stat'%pid).read_text().rsplit(')',1)[1].split()[19])
backup=Path(tempfile.mkdtemp(prefix='vzcriu-reconcile-', dir='/tmp'))
shutil.copy2(p,backup/'crun-status.json')
alive=Path('/run/libpod/alive'); shutil.copy2(alive,backup/'alive')
d['process-start-time']=start
q=p.with_suffix('.migration-tmp'); q.write_text(json.dumps(d)); os.replace(q,p)
alive.write_bytes(Path('/proc/sys/kernel/random/boot_id').read_bytes())
print(json.dumps({'pid':pid,'start_time':start,'backup':str(backup)}))
'''

def restore(name, job):
    folder = BASE / job
    before = json.loads((folder / 'before.json').read_text())
    # Fail rather than replace any existing destination container.
    r = subprocess.run(P + ['container', 'exists', name], env=ENV)
    if r.returncode != 1:
        raise RuntimeError('Destination must not contain the requested container')
    pod('container', 'restore', '--import=' + str(folder / 'checkpoint.tar'), '--file-locks', '--keep')
    d = owned(name)
    if d['Id'] != before['outer_id'] or not d['State']['Running']:
        raise RuntimeError('Restored identity/state mismatch')
    # Only a confirmed restored lab workload may have its runtime references rebound.
    result = run(P + ['exec', '-i', name, 'python3', '-', json.dumps(before)], RECONCILE)
    after = proof(name)
    if after['uuid'] != before['uuid'] or after['counter'] < before['counter']:
        raise RuntimeError('Memory continuity check failed')
    deadline = time.monotonic() + 10
    first = after['counter']
    while after['counter'] <= first and time.monotonic() < deadline:
        time.sleep(0.3)
        after = proof(name)
    if after['counter'] <= first or after['uuid'] != before['uuid']:
        raise RuntimeError('No fresh counter progress with original UUID')
    marker = inside(name, *INNER, 'exec', 'counter', 'sh', '-c', 'echo INNER_EXEC_OK').strip()
    if marker != 'INNER_EXEC_OK':
        raise RuntimeError('Inner execution check failed')
    return {'after': after, 'reconciliation': json.loads(result), 'exec': marker}

def identity():
    return {'machine_id': Path('/etc/machine-id').read_text().strip(),
            'compatibility': {'kernel': os.uname().release, 'architecture': os.uname().machine,
            'criu_sha256': hashlib.sha256(RUNTIME['real'].read_bytes()).hexdigest(),
            'podman': run(['podman', '--version']).strip(),
            'crun': run(['crun', '--version']).splitlines()[0],
            'image': json.loads(pod('image', 'inspect', 'localhost/migration-lab:extended'))[0]['Id']}}

def preflight(name):
    if not BASE.is_dir() or not RUNTIME['real'].is_file():
        raise RuntimeError('Target not initialized')
    r = subprocess.run(P + ['container', 'exists', name], env=ENV)
    if r.returncode != 1:
        raise RuntimeError('Destination container already exists or store unavailable')
    run([str(RUNTIME['wrapper']), '--version'])
    return dict(identity(), ready=True)

def retire(name):
    if owned(name)['State']['Running']:
        raise RuntimeError('Refusing to retire a running workload')
    pod('rm', name)
    return {'retired_stopped_lab_container': name}

def main():
    a = argparse.ArgumentParser(description=__doc__)
    a.add_argument('action', choices=['init', 'create', 'proof', 'checkpoint', 'restore', 'state', 'preflight', 'identity', 'retire'])
    a.add_argument('--name', default='vzkit-demo')
    a.add_argument('--job', default='')
    a.add_argument('--image', default='localhost/migration-lab:extended')
    o = a.parse_args()
    for value in [o.name, o.job]:
        if value and not re.fullmatch(r'[a-zA-Z0-9_-]+', value):
            a.error('Invalid name/job identifier')
    if os.geteuid() != 0:
        a.error('Run with sudo inside the dedicated VM')
    if o.action in ['checkpoint', 'restore'] and not o.job:
        a.error('--job is required')
    if o.action == 'identity': result = identity()
    elif o.action == 'preflight': result = preflight(o.name)
    elif o.action == 'retire': result = retire(o.name)
    elif o.action == 'init': result = init(o.image)
    elif o.action == 'create': result = create(o.name, o.image)
    elif o.action == 'proof': result = proof(o.name)
    elif o.action == 'state': result = {'running': owned(o.name)['State']['Running']}
    elif o.action == 'checkpoint': result = checkpoint(o.name, o.job)
    else: result = restore(o.name, o.job)
    print(json.dumps(result))

if __name__ == '__main__':
    main()
