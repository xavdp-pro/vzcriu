#!/bin/sh
# Read-only verification after APT installation on Debian 13 amd64.
set -eu
expected=4ecb663e7e3b019cdfa534c0d4cce4134938f87ae3b45cac35a7481c0a947cd4
actual=$(sha256sum /usr/lib/podmesh-vzcriu/criu | cut -d' ' -f1)
[ "$actual" = "$expected" ]
podmesh-vzcriu --version
if ldd /usr/lib/podmesh-vzcriu/criu | grep -q 'not found'; then exit 1; fi
if dpkg-query -L podmesh-vzcriu | grep -q '^/usr/sbin/criu$\|^/usr/bin/criu$'; then exit 1; fi
printf '%s\n' 'Runtime package integrity and dependency checks passed; migration not exercised by this check.'
