#!/bin/bash
# Run in the root of the patched vzcriu source tree on Debian 13 amd64.
set -euo pipefail
umask 022
version=3.15.5.3+podmesh1~experimental1
[[ $(dpkg --print-architecture) == amd64 ]]
make -j2 WERROR=0 criu
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
chmod 755 "$work"
mkdir -p "$work/DEBIAN" "$work/usr/lib/podmesh-vzcriu" "$work/usr/bin" "$work/usr/share/doc/podmesh-vzcriu"
install -m755 criu/criu "$work/usr/lib/podmesh-vzcriu/criu"
cat > "$work/usr/bin/podmesh-vzcriu" <<'WRAPPER'
#!/bin/sh
# Keep the experimental runtime separate from distribution CRIU.
export GLIBC_TUNABLES=glibc.pthread.rseq=0
exec /usr/lib/podmesh-vzcriu/criu "$@"
WRAPPER
chmod 755 "$work/usr/bin/podmesh-vzcriu"
install -m644 COPYING "$work/usr/share/doc/podmesh-vzcriu/copyright"
install -m644 contrib/nested-podman/README.md "$work/usr/share/doc/podmesh-vzcriu/README.md"
cat > "$work/DEBIAN/control" <<CONTROL
Package: podmesh-vzcriu
Version: $version
Architecture: amd64
Section: admin
Priority: optional
Maintainer: Xavier de Poorter <xavier@xavdp.pro>
Depends: libc6 (>= 2.39), libbsd0, libbpf1, libgnutls30t64, libprotobuf-c1, libnl-3-200, libnet1, ipset
Description: Experimental patched vzcriu for isolated PodMesh migration tests
 Installs a separately named runtime without replacing system CRIU.
 Supports only the qualified nested Podman laboratory workflow.
 Arbitrary workloads, host failure and production use are not validated.
CONTROL
mkdir -p build/deb
dpkg-deb --root-owner-group --build "$work" "build/deb/podmesh-vzcriu_${version}_amd64.deb"
