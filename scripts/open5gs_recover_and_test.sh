#!/usr/bin/env bash
set -euo pipefail

# Usage:
#   ./groupStudies/scripts/open5gs_recover_and_test.sh [OPEN5GS_DIR] [MESON_TEST_ARGS...]
# Examples:
#   ./groupStudies/scripts/open5gs_recover_and_test.sh
#   ./groupStudies/scripts/open5gs_recover_and_test.sh /home/testing/prj/open5gs registration
#   ./groupStudies/scripts/open5gs_recover_and_test.sh /home/testing/prj/open5gs --suite 5gc

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORKSPACE_DIR="$(cd "${SCRIPT_DIR}/../.." && pwd)"
OPEN5GS_DIR="${1:-${WORKSPACE_DIR}/open5gs}"

if [[ -d "${OPEN5GS_DIR}/build" ]]; then
    shift || true
else
    OPEN5GS_DIR="${WORKSPACE_DIR}/open5gs"
fi

if [[ ! -d "${OPEN5GS_DIR}/build" ]]; then
    echo "ERROR: Could not find Open5GS build directory under: ${OPEN5GS_DIR}" >&2
    echo "Pass explicit OPEN5GS_DIR as first argument." >&2
    exit 1
fi

echo "[INFO] Open5GS dir: ${OPEN5GS_DIR}"
echo "[INFO] Acquiring sudo credentials..."
sudo -v

echo "[INFO] Stopping stale Open5GS daemons..."
sudo pkill -9 -f 'open5gs-(nrfd|scpd|amfd|smfd|upfd|ausfd|udmd|udrd|pcfd|nssfd|bsfd|mmed|sgwcd|sgwud|hssd|pcrfd)' || true
sudo pkill -9 -f '/open5gs/build/tests/app/5gc' || true
sudo pkill -9 -f '/open5gs/build/tests/(attach|registration)' || true
sleep 1

echo "[INFO] Ensuring MongoDB is active..."
sudo systemctl start mongod || true

# Tests need TUN setup; use netconf helper if present.
echo "[INFO] Ensuring ogstun exists and is up..."
if [[ -x "${OPEN5GS_DIR}/misc/netconf.sh" ]]; then
    sudo "${OPEN5GS_DIR}/misc/netconf.sh" || true
fi
sudo ip tuntap add name ogstun mode tun 2>/dev/null || true
sudo ip addr add 10.45.0.1/16 dev ogstun 2>/dev/null || true
sudo ip addr add 2001:db8:cafe::1/48 dev ogstun 2>/dev/null || true
sudo ip link set ogstun up

echo "[INFO] Pre-test socket check (should be mostly empty)..."
ss -ltnp 2>/dev/null | grep -E ':(3868|38412|7777|9090|2123|2152|8805)\\b' || true

echo "[INFO] Running tests with sudo to avoid /dev/net/tun EPERM..."
cd "${OPEN5GS_DIR}/build"
sudo meson test -v --print-errorlogs "$@"
