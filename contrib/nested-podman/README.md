# Experimental nested Podman checkpoint/restore laboratory

## Attribution

This experimental adaptation and automation kit were developed by **Xavier de
Poorter (xavdp-pro), in collaboration with OpenAI Codex (GPT-6)**. Xavier defined
the objectives and directed the experiments; Codex implemented the adaptations,
automation and documented laboratory checks under his direction.

The original CRIU and OpenVZ/Virtuozzo vzcriu code, including the existing nested
PID namespace support, belongs to its upstream contributors. Our contribution is
the Debian 13 compatibility work, the nested Podman demonstration workflow,
runtime reconciliation and the published test procedure described here. Original
copyright notices and licensing remain unchanged. This fork does not imply
upstream review, endorsement or acceptance of our experimental changes.

## Overview

This kit investigates migration of an outer Podman container containing a running
inner Podman container. It is **not production-qualified**. It uses a patched
OpenVZ/vzcriu (upstream base `6fe1b0e`), not current upstream CRIU.

Use two disposable Debian 13 VMs with matching CPU features, kernel, Podman,
crun, images, and this CRIU binary. The initial laboratory used Podman 5.4.2,
Debian kernel 6.12.107+deb13-amd64, 2 vCPU, 4 GiB RAM and 24 GiB disk per VM.
The hypervisor is not part of the implementation. Do not install this on a
production host. The outer container is privileged: the VM is its safety boundary.

## Scope inventory

| ID | Capability | Acceptance |
| --- | --- | --- |
| K1 | Build/install patched CRIU without replacing system CRIU | Same binary checksum on both guests |
| K2 | Create disposable nested VFS workload | Counter and memory-generated UUID visible |
| K3 | Checkpoint and transfer | Source stopped; archive checksums match |
| K4 | Restore nested processes | UUID preserved; counter progresses |
| K5 | Reconcile inner runtime metadata | Inner logs and exec work without workload restart |
| K6 | Repeat migration | Separate evidence for each direction |
| K7 | Failure visibility | Persisted FAILED report; no automatic source restart |
| K8 | Share reproducible procedure | Public sources, limitations and measured results |

Only the kit-created counter workload is supported. No networking, persistent
external volumes, rootless operation, arbitrary applications, high availability,
or live service guarantee is provided. Inner cgroup namespaces are shared and
inner cgroup management disabled; PID namespaces remain separate.

## Build and prepare

On the build VM, from this repository root:

```sh
sh contrib/nested-podman/build.sh
```

Install Podman, crun, Python 3, ipset and the binary's shared-library dependencies
on both guests (use `ldd criu/criu` to verify). Build the outer image once on SOURCE:

```sh
sudo podman build -t localhost/migration-lab:extended -f contrib/nested-podman/Containerfile .
sudo podman pull docker.io/library/alpine:3.22
sudo podman save -o /tmp/vzkit-images.tar localhost/migration-lab:extended docker.io/library/alpine:3.22
```

Copy that saved image archive to TARGET and load it into its normal Podman store:

```sh
scp /tmp/vzkit-images.tar lab@TARGET:/tmp/vzkit-images.tar
ssh lab@TARGET sudo podman load -i /tmp/vzkit-images.tar
```

Do not build independently on the target: preflight requires identical image IDs.

Image tags and distribution package repositories are mutable. Record actual image
IDs and package versions; for reproducibility beyond this lab, distribute the
same saved image and pin repository snapshots. Do not claim bit-reproducible builds.

On a controller with Python 3 and SSH access (passwordless sudo in the test VMs):

```sh
python3 contrib/nested-podman/lab.py install --source lab@SOURCE --binary ./criu/criu
python3 contrib/nested-podman/lab.py install --source lab@TARGET --binary ./criu/criu
python3 contrib/nested-podman/lab.py init --source lab@SOURCE
python3 contrib/nested-podman/lab.py init --source lab@TARGET
python3 contrib/nested-podman/lab.py create --source lab@SOURCE --name demo
python3 contrib/nested-podman/lab.py migrate --source lab@SOURCE --target lab@TARGET --name demo
```

`build.sh` builds the fork. `node.py` performs guest-local operations in separate
VFS stores. `lab.py` drives the sequence and writes local evidence. No manager
service or GUI is included. `/opt/vzcriu-kit/bin/criu` wraps the experimental binary
with `GLIBC_TUNABLES=glibc.pthread.rseq=0`; system CRIU is not replaced. This avoids
inherited libc rseq registration in the old restorer; it does **not** implement
complete checkpoint/restore of arbitrary rseq-using workloads.

## Migration sequence and evidence

The controller records the workload UUID, counter and inner identity, checkpoints
without leave-running, checks the source stopped, copies the archive through the
controller, compares SHA-256, restores on the target, reconciles runtime references,
and checks logs and inner exec. This is stop/copy/restore, not memory pre-copy.
Archives are retained and may contain sensitive process memory: keep evidence
private. Never commit archives, raw customer logs, or secrets to this public fork.

On failure, consult `vzkit-evidence/<job>/result.json` and the target restore.log.
The source is **not automatically restarted**: a timeout can leave the target
partially active. Determine which side is active before recovery. The initial kit
requires an empty destination name; it never deletes an existing destination.
No service continuity claim follows from the command duration.

## Why the patches exist

- Modern libc/libbpf and cgroup-v2 compatibility for the older fork.
- Standard Linux move_mount group handling instead of the Virtuozzo-only syscall.
- Resolve proc-fd-relative UNIX socket names during dump. This changes the restored
  getsockname pathname; applications relying on that exact spelling are unqualified.
- Use thread-self when restoring LSM attributes across nested PID namespaces.
- After validating the known resumed workload, update inner Podman's cached boot ID
  and crun's cached process start time. Backups remain inside the restored outer
  container. This is deliberately not a generic repair of arbitrary runtime state.

See RESULTS.md for actual execution evidence and remaining gaps. A successful
single run is not a guarantee for arbitrary workloads or kernels.

## Return trip

After verifying that TARGET resumed successfully, retire **only the stopped lab
copy** on SOURCE, preserving its checkpoint evidence. Reverse the direction:

```sh
python3 contrib/nested-podman/lab.py retire --source lab@SOURCE --name demo
python3 contrib/nested-podman/lab.py migrate --source lab@TARGET --target lab@SOURCE --name demo
```

`retire` refuses a running or unlabelled container. Operate this laboratory from
one controller, serially; concurrent migrations and distributed fencing are not
implemented. Destination preflight verifies distinct machine IDs, matching
kernel, architecture, CRIU hash, runtime versions and outer image ID. CPU feature
compatibility and available disk/memory remain operator prerequisites. An empty
store is not created implicitly by migration; run init first.

## Experimental Debian runtime package

`build-runtime-deb.sh` builds `podmesh-vzcriu` on Debian 13 amd64 from this source tree. It installs `/usr/lib/podmesh-vzcriu/criu` and the explicitly named `/usr/bin/podmesh-vzcriu` wrapper, without replacing the distribution `criu`. The wrapper applies the same experimental rseq workaround described above. The package contains the upstream license and declares its shared-library dependencies.

This runtime package alone does not install the complete migration controller, prepare images or enable migration in the PodMesh service. Existing `/opt/vzcriu-kit` installations are not rewritten. The first packaged binary was checked to match the previously tested runtime SHA-256 `4ecb663e7e3b019cdfa534c0d4cce4134938f87ae3b45cac35a7481c0a947cd4`.
