# podmesh-vzcriu-helpers / podmesh-vzcriu-helpers-node (Debian packages)

## What these packages are

Two packages install the controller/helper scripts from the experimental
nested Podman checkpoint/restore laboratory described in `README.md`,
`RESULTS.md` and `REPLICATION.md` in this directory, split along where
Podman and crun are actually required:

- **`podmesh-vzcriu-helpers`** (controller side) — `lab.py` and
  `replication.py`, exposed as `podmesh-vzcriu-lab` and
  `podmesh-vzcriu-replication`. Drives node hosts over SSH; does not itself
  call Podman or crun, so it only depends on `python3` and `openssh-client`.
- **`podmesh-vzcriu-helpers-node`** (node side) — `node.py`, exposed as
  `podmesh-vzcriu-node`, plus a private-path `criu` shim used only so
  Podman's own `PATH` lookup for a binary literally named `criu` keeps
  working. `node.py` genuinely invokes Podman, crun and the packaged CRIU
  runtime, so this package hard-`Depends` on `podmesh-vzcriu (>= 3.15.5.3+podmesh1~experimental1)`,
  `python3`, `podman` and `crun` — not a soft `Recommends`.

A single host acting as both controller and node installs both packages.
Neither package rebuilds, vendors or replaces `podmesh-vzcriu`.

## What these packages are not

- Not a generic migration or high-availability tool. They support only the
  single disposable, network-free, kit-created "counter" workload described
  in `README.md`'s scope inventory (K1-K8). No networking, persistent
  external volumes, rootless operation, arbitrary application support, or
  live-service guarantee is provided.
- Not a replacement for the distribution's `criu` package. The packaged
  runtime is installed under a private path and invoked through the
  explicitly named `podmesh-vzcriu` wrapper; system CRIU is never touched.
  `podmesh-vzcriu-helpers-node`'s own `criu` shim lives at
  `/opt/podmesh-vzcriu-kit/bin/criu`, never under `/usr/bin` or `/usr/sbin`.
- Not a rewrite of, or replacement for, an existing manual `/opt/vzcriu-kit`
  installation built from source. Installing either package does not modify
  or remove `/opt/vzcriu-kit` in any way, and both can coexist on the same
  host with the manual kit.
- Not a clean-room reproducible build: image tags and distribution package
  repositories are mutable upstream. Pin package versions and distribute the
  same saved container image if you need to reproduce a specific run; see
  "Image preparation" below.

## Image preparation

Before running any lab command, prepare and distribute the container images
exactly as documented in `README.md` under "Build and prepare":

1. Build the outer image once on the SOURCE host:
   `sudo podman build -t localhost/migration-lab:extended -f Containerfile .`
2. Pull the pinned inner image: `sudo podman pull docker.io/library/alpine:3.22`
3. Save both images to a single archive and copy that archive to every other
   host that will participate (`podman save`, `scp`, `podman load`). Do not
   build independently on each host — preflight requires identical image IDs
   across hosts, and this package does not perform that check for you at
   install time.

Record the actual image IDs and package versions you used; this package does
not pin or vendor them.

## Out of the box: no environment variables required

The installed commands select the packaged runtime/mode themselves, before
they ever hand off to `node.py`/`lab.py`/`replication.py`:

- `podmesh-vzcriu-node` sets `PODMESH_VZCRIU_RUNTIME=packaged` (only if not
  already set by the caller) before running `node.py`, so `node.py`'s own
  autodetection is bypassed and the packaged `/usr/lib/podmesh-vzcriu/criu`
  runtime is used unconditionally.
- `podmesh-vzcriu-lab` and `podmesh-vzcriu-replication` do the same. On the
  controller, `PODMESH_VZCRIU_RUNTIME=packaged` makes `lab.py`/`replication.py`
  talk to `podmesh-vzcriu-node` on the remote node host (instead of the
  manual `sudo python3 /opt/vzcriu-kit/node.py`) and makes
  `replication.py`'s remote snippets import `node.py` from
  `/opt/podmesh-vzcriu-kit/lib` on the remote host (instead of
  `/opt/vzcriu-kit`).

A clean target machine with only the Debian packages installed therefore
works correctly using just the named commands — no exports, no config file,
no flags. `PODMESH_VZCRIU_RUNTIME` (and, on the controller,
`PODMESH_VZCRIU_NODE_CMD`) remain available as explicit overrides, e.g. to
point `podmesh-vzcriu-lab` at node hosts still running the manual kit:

```sh
PODMESH_VZCRIU_RUNTIME=manual podmesh-vzcriu-lab migrate --source lab@A --target lab@B --name demo
# equivalently, for a non-standard remote command:
PODMESH_VZCRIU_NODE_CMD='sudo python3 /opt/vzcriu-kit/node.py' podmesh-vzcriu-lab migrate --source lab@A --target lab@B --name demo
```

Running `python3 lab.py ...`/`python3 node.py ...` directly from a checkout
(not through the installed wrapper commands) leaves `PODMESH_VZCRIU_RUNTIME`
unset and keeps the original manual-kit default, unchanged.

## Deterministic remote dispatch (survives sudo's environment reset)

`replication.py`'s two remote snippets (`SNAPSHOT`, `STOP`) run as
`sudo python3 -c <snippet> <lib_dir> <mode> <name> [job]` over SSH. `sudo`
on the target host typically resets/filters the environment, so the
snippet cannot rely on an inherited environment variable — and on a host
where both a manual and a packaged kit happen to exist, guessing (e.g.
trying several `sys.path` candidates in some order) would be
non-deterministic and could silently pick the wrong one. Two separate
decisions are involved, and both are resolved once by the controller
(`lab.resolved_mode()`) and passed on the command line, exactly like
`name`/`job` already were:

1. **Which `node.py` file to import** (`lib_dir`, the snippet's first
   positional argument): `sys.path.insert(0, sys.argv[1])` before `import
   node`.
2. **What runtime mode that `node.py` itself selects once imported**
   (`mode`, the second positional argument): `node.py`'s own module-level
   `RUNTIME = _detect_runtime()` reads `PODMESH_VZCRIU_RUNTIME` from the
   process environment the instant `import node` runs. Since that
   environment is the freshly-`sudo`'d remote process's own — not
   something the controller can set from the outside — the snippet does
   `os.environ['PODMESH_VZCRIU_RUNTIME'] = sys.argv[2]` **before** `import
   node as n`, forcing the same mode the controller resolved. Setting the
   variable is safe here because it happens inside the already-running
   remote Python process, after the `sudo` hop, not across it.

Getting only (1) right is not enough: an earlier revision of this fix did
exactly that — it made the correct `node.py` file load, but that file's own
`_detect_runtime()` still ran with no `PODMESH_VZCRIU_RUNTIME` in its
environment and fell back to its own autodetection (which prefers the
manual kit), silently ignoring whatever mode the controller had actually
resolved on a host where both layouts were present. Both values are now
forced from argv, deterministically and unconditionally, unaffected by
`sudo`'s environment handling.

## Verification

`check-helpers-package.sh` performs a read-only, offline-safe check after
installation: it confirms the expected files exist, that neither package
wrote into `/opt/vzcriu-kit`, that `podmesh-vzcriu-lab` defaults
`PODMESH_VZCRIU_RUNTIME` to `packaged` with no environment set, and — if
`podmesh-vzcriu-helpers-node` is also installed — that runtime detection
resolves to the packaged `podmesh-vzcriu` binary. It does not create,
checkpoint or restore any workload.

Static/offline development checks (no root, no containers, no CRIU, no
network) live in `tests/test_helpers_dispatch.py` and are run by
`build-helpers-deb.sh --check`. They include: `node.py`'s own runtime
autodetection; `lab.py`'s controller-mode resolution; the installed
wrapper scripts' out-of-the-box packaged-mode selection (executed directly
as subprocesses, not merely re-implemented in Python); and a simulation of
the SNAPSHOT/STOP remote dispatch with a fully wiped environment (`env -i`)
and both a manual-layout and a packaged-layout stub present at once,
proving the resolved mode — not insertion order or environment
inheritance — determines which one loads.
