#!/bin/sh
# Run inside a dedicated Debian 13 build VM, from the repository root.
set -eu
sudo apt-get update
sudo apt-get install -y build-essential pkg-config libprotobuf-c-dev protobuf-c-compiler libprotobuf-dev protobuf-compiler libnl-3-dev libnet-dev libcap-dev libaio-dev libgnutls28-dev libnftnl-dev libbsd-dev python3-dev libbpf-dev ipset
make -j2 WERROR=0 criu
