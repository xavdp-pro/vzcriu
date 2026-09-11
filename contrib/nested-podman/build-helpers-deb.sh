#!/bin/bash
# Build the podmesh-vzcriu-helpers* packages: the node.py/lab.py/replication.py
# controller scripts from this directory, packaged separately from the
# podmesh-vzcriu runtime (see build-runtime-deb.sh). Run this script with
# bash (it uses bash-only syntax; "sh build-helpers-deb.sh" will fail under
# dash) from the repository root on Debian 13 amd64, or anywhere dpkg-deb is
# available for an amd64 archive.
#
# Two binary packages are produced, matching where podman/crun are actually
# required:
#   - podmesh-vzcriu-helpers        (controller side: lab.py, replication.py;
#                                     only needs python3 + ssh, not podman/crun)
#   - podmesh-vzcriu-helpers-node   (node side: node.py, the private "criu"
#                                     shim; hard-depends on podmesh-vzcriu,
#                                     podman and crun, which node.py actually
#                                     invokes)
# A single host acting as both controller and node installs both packages.
#
# This script does NOT build or install podmesh-vzcriu: the node package only
# depends on it. It does not touch /opt/vzcriu-kit (the manual/source-tree
# kit) and does not replace the distribution criu package.
#
# Usage:
#   bash contrib/nested-podman/build-helpers-deb.sh          # build both .debs
#   bash contrib/nested-podman/build-helpers-deb.sh --check   # offline tests only, no package built
set -euo pipefail
umask 022

here="$(cd "$(dirname "$0")" && pwd)"
repo_root="$(cd "$here/../.." && pwd)"
version=1.0.0+podmesh1~experimental1
# Minimum podmesh-vzcriu version this helper release was written and tested
# against; keeps podmesh-vzcriu-helpers-node from being installed alongside
# an older/incompatible runtime build.
runtime_min_version=3.15.5.3+podmesh1~experimental1
pkg_base=podmesh-vzcriu-helpers
pkg_node=podmesh-vzcriu-helpers-node

run_checks() {
    echo "== py_compile =="
    python3 -m py_compile "$here/node.py" "$here/lab.py" "$here/replication.py"
    echo "== offline unit tests (no podman/criu/ssh/root required) =="
    python3 -m unittest discover -s "$here/tests" -p 'test_*.py' -v
    echo "== shell syntax check =="
    for script in "$here"/build*.sh "$here"/check*.sh "$here"/packaging/*; do
        [ -f "$script" ] || continue
        bash -n "$script"
        echo "  ok: $script"
    done
    if command -v shellcheck >/dev/null 2>&1; then
        echo "== shellcheck (must pass; a real finding fails this check) =="
        shellcheck "$here"/build*.sh "$here"/check*.sh "$here"/packaging/*
        echo "shellcheck: no issues found"
    else
        echo "shellcheck not installed; skipped. This is NOT the same as shellcheck having passed."
    fi
}

run_checks

if [ "${1:-}" = "--check" ]; then
    exit 0
fi

[[ $(dpkg --print-architecture) == amd64 ]]

work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT

build_base_package() {
    local root="$work/base"
    mkdir -p "$root"
    chmod 755 "$root"
    local libdir="$root/opt/podmesh-vzcriu-kit/lib"
    local bindir="$root/usr/bin"
    local docdir="$root/usr/share/doc/$pkg_base"
    mkdir -p "$root/DEBIAN" "$libdir" "$bindir" "$docdir"

    install -m755 "$here/lab.py" "$libdir/lab.py"
    install -m755 "$here/replication.py" "$libdir/replication.py"
    install -m755 "$here/packaging/podmesh-vzcriu-lab" "$bindir/podmesh-vzcriu-lab"
    install -m755 "$here/packaging/podmesh-vzcriu-replication" "$bindir/podmesh-vzcriu-replication"

    install -m644 "$repo_root/COPYING" "$docdir/copyright"
    install -m644 "$here/README.md" "$docdir/README.md"
    if [ -f "$here/README.Debian-helpers.md" ]; then
        install -m644 "$here/README.Debian-helpers.md" "$docdir/README.Debian"
    fi

    cat > "$root/DEBIAN/control" <<CONTROL
Package: $pkg_base
Version: $version
Architecture: amd64
Section: admin
Priority: optional
Maintainer: Xavier de Poorter <xavier@xavdp.pro>
Depends: python3 (>= 3.11), openssh-client
Description: Controller scripts for the experimental podmesh-vzcriu lab
 Installs lab.py and replication.py from the nested-podman migration
 laboratory kit under /opt/podmesh-vzcriu-kit, plus the explicitly named
 commands podmesh-vzcriu-lab and podmesh-vzcriu-replication. Only drives
 remote node hosts over SSH; does not itself require Podman or crun. Install
 podmesh-vzcriu-helpers-node on the node hosts being driven. Workload-
 specific: supports only the disposable, network-free nested Podman counter
 workload documented in README.Debian; it is not a generic migration or HA
 solution.
CONTROL

    mkdir -p "$repo_root/build/deb"
    dpkg-deb --root-owner-group --build "$root" "$repo_root/build/deb/${pkg_base}_${version}_amd64.deb"
}

build_node_package() {
    local root="$work/node"
    mkdir -p "$root"
    chmod 755 "$root"
    local libdir="$root/opt/podmesh-vzcriu-kit/lib"
    local bindir_priv="$root/opt/podmesh-vzcriu-kit/bin"
    local bindir_pub="$root/usr/bin"
    local docdir="$root/usr/share/doc/$pkg_node"
    mkdir -p "$root/DEBIAN" "$libdir" "$bindir_priv" "$bindir_pub" "$docdir"

    install -m755 "$here/node.py" "$libdir/node.py"
    install -m755 "$here/packaging/criu-shim" "$bindir_priv/criu"
    install -m755 "$here/packaging/podmesh-vzcriu-node" "$bindir_pub/podmesh-vzcriu-node"

    install -m644 "$repo_root/COPYING" "$docdir/copyright"
    install -m644 "$here/README.md" "$docdir/README.md"
    if [ -f "$here/README.Debian-helpers.md" ]; then
        install -m644 "$here/README.Debian-helpers.md" "$docdir/README.Debian"
    fi

    cat > "$root/DEBIAN/control" <<CONTROL
Package: $pkg_node
Version: $version
Architecture: amd64
Section: admin
Priority: optional
Maintainer: Xavier de Poorter <xavier@xavdp.pro>
Depends: podmesh-vzcriu (>= $runtime_min_version), python3 (>= 3.11), podman, crun
Description: Node-host helper for the experimental podmesh-vzcriu lab
 Installs node.py from the nested-podman migration laboratory kit under
 /opt/podmesh-vzcriu-kit, the explicitly named command podmesh-vzcriu-node,
 and a private-path "criu" shim used only for Podman's own PATH lookup
 (never installed under /usr/bin or /usr/sbin, so the distribution criu
 package is never shadowed). Depends on the separately packaged
 podmesh-vzcriu runtime, Podman and crun, which node.py actually invokes at
 runtime, instead of only recommending them. Workload-specific: supports
 only the disposable, network-free nested Podman counter workload documented
 in README.Debian; it is not a generic migration or HA solution.
CONTROL

    mkdir -p "$repo_root/build/deb"
    dpkg-deb --root-owner-group --build "$root" "$repo_root/build/deb/${pkg_node}_${version}_amd64.deb"
}

build_base_package
build_node_package
