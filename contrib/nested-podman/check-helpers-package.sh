#!/bin/sh
# Read-only verification after installing podmesh-vzcriu-helpers (and,
# optionally, podmesh-vzcriu-helpers-node) via APT. Confirms the packaged
# layout is present and that the installed commands select the packaged
# runtime/mode with NO environment variables set, without running any
# checkpoint/restore operation.
set -eu

test -x /opt/podmesh-vzcriu-kit/lib/lab.py
test -x /opt/podmesh-vzcriu-kit/lib/replication.py
test -x /usr/bin/podmesh-vzcriu-lab
test -x /usr/bin/podmesh-vzcriu-replication
if dpkg-query -L podmesh-vzcriu-helpers | grep -q '^/opt/vzcriu-kit'; then
  echo "podmesh-vzcriu-helpers must not write into the manual /opt/vzcriu-kit layout" >&2
  exit 1
fi

# lab.py/replication.py resolve their controller mode from
# PODMESH_VZCRIU_RUNTIME; the installed podmesh-vzcriu-lab wrapper must set
# it to "packaged" itself, with no environment variables set by the caller.
# Point the real wrapper at a throwaway probe script (its documented
# PODMESH_VZCRIU_LAB_PY_OVERRIDE test hook) that just reports what the
# wrapper exported, and confirm it is "packaged" with a clean environment.
probe=$(mktemp)
trap 'rm -f "$probe"' EXIT
cat > "$probe" <<'PY'
import os
print(os.environ.get('PODMESH_VZCRIU_RUNTIME', ''))
PY
resolved=$(env -u PODMESH_VZCRIU_RUNTIME -u PODMESH_VZCRIU_NODE_CMD \
  PODMESH_VZCRIU_LAB_PY_OVERRIDE="$probe" /usr/bin/podmesh-vzcriu-lab)
if [ "$resolved" != packaged ]; then
  echo "podmesh-vzcriu-lab did not default PODMESH_VZCRIU_RUNTIME to packaged (got '$resolved')" >&2
  exit 1
fi

if [ -x /opt/podmesh-vzcriu-kit/bin/criu ] && [ -x /opt/podmesh-vzcriu-kit/lib/node.py ] && [ -x /usr/bin/podmesh-vzcriu-node ]; then
  echo "podmesh-vzcriu-helpers-node also installed; checking node side too."
  if dpkg-query -L podmesh-vzcriu-helpers-node | grep -q '^/opt/vzcriu-kit'; then
    echo "podmesh-vzcriu-helpers-node must not write into the manual /opt/vzcriu-kit layout" >&2
    exit 1
  fi
  if command -v podmesh-vzcriu >/dev/null 2>&1; then
    env PODMESH_VZCRIU_RUNTIME=packaged python3 -c "
import sys
sys.path.insert(0, '/opt/podmesh-vzcriu-kit/lib')
import node
r = node._detect_runtime()
assert r['wrapper'] == __import__('pathlib').Path('/usr/bin/podmesh-vzcriu'), r
assert r['real'] == __import__('pathlib').Path('/usr/lib/podmesh-vzcriu/criu'), r
print('packaged runtime resolves to', r)
"
    /opt/podmesh-vzcriu-kit/bin/criu --version
  else
    echo "podmesh-vzcriu not on PATH; Depends should have installed it." >&2
    exit 1
  fi
else
  echo "podmesh-vzcriu-helpers-node not installed on this host; node-side checks skipped." >&2
fi

printf '%s\n' 'Helper package layout and installed-command mode selection verified; no migration exercised by this check.'
