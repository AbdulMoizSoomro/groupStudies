# Comprehensive E2E O-RAN + Open5GS Runbook (Validated in This Workspace)

depreciated: use `_Setup Guide srsRAN + OpenWrt` instead, which is more concise and focused on the core steps. This document is retained for reference but may contain redundant or outdated information.

Date: 2026-03-09  
Workspace: `/home/testing/prj`

Dynamic mode: export once, then reuse `"$WS"`-based commands in all sections.

This document provides a full, practical, end-to-end setup for:
- Open5GS 5G Core (from source)
- O-RAN SC Near-RT RIC (docker compose)
- srsRAN gNB + srsUE using ZeroMQ (no RF hardware)
- USRP + COTS UE path (hardware option)
- KPIMON xApp lifecycle
- End-to-end UE PDU session and ping validation

It also includes the common failure cases and exact fixes.

---

## 1) Target Architecture

- **Core**: Open5GS from source (`open5gs/build/tests/app/5gc`)
- **RIC**: `oran-sc-ric/docker-compose.yml` stack
- **RAN**: `srsRAN_Project` gNB (ZMQ)
- **UE**: `srsRAN_4G` srsUE (ZMQ + netns `ue1`)
- **Data path test**: `ue1 -> 10.45.0.1` ping

---

## 1.1) Dynamic Runtime Variables (Copy Once Per Shell)

Use this block in each terminal before running commands. It removes hardcoded paths and lets you switch subscriber profile quickly.

```bash
# Workspace root. Override if needed.
export WS="${WS:-$PWD}"

# Optional explicit roots (auto-derived from WS).
export OPEN5GS_ROOT="${OPEN5GS_ROOT:-$WS/open5gs}"
export RIC_ROOT="${RIC_ROOT:-$WS/oran-sc-ric}"
export GNB_ROOT="${GNB_ROOT:-$WS/srsRAN_Project}"
export UE_ROOT="${UE_ROOT:-$WS/srsRAN_4G}"

# Networking/runtime parameters.
export OGS_TUN="${OGS_TUN:-ogstun}"
export UE_NS="${UE_NS:-ue1}"
export GNB_BIND_ADDR="${GNB_BIND_ADDR:-10.0.2.1}"
export CORE_GW_IP="${CORE_GW_IP:-10.45.0.1}"

# Select subscriber profile: A or B.
export PROFILE="${PROFILE:-B}"
if [ "$PROFILE" = "B" ]; then
  export IMSI="001010123456780"
  export KI="00112233445566778899aabbccddeeff"
  export OPC="63BFA50EE6523365FF14C1F45F88737D"
  export APN="srsapn"
else
  export IMSI="001010000000101"
  export KI="0C0A34601D4F07677303652C0462535B"
  export OPC="63BFA50EE6523365FF14C1F45F88737B"
  export APN="internet"
fi

# Dynamic values discovered at runtime.
export E2_IP="${E2_IP:-$(docker inspect -f '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}' ric_e2term 2>/dev/null || true)}"
export OUT_IFACE="${OUT_IFACE:-$(ip route show default | awk '{print $5; exit}')}"

printf 'WS=%s\nPROFILE=%s IMSI=%s APN=%s\nE2_IP=%s OUT_IFACE=%s\n' \
  "$WS" "$PROFILE" "$IMSI" "$APN" "$E2_IP" "$OUT_IFACE"
```

---

## 2) Known Good Identity/Session Values

Use one profile consistently across Open5GS, gNB, UE, and subscriber DB.

- PLMN: `00101`
- TAC: `7`

Profile A :
- IMSI: `001010000000101`
- Ki: `0C0A34601D4F07677303652C0462535B`
- OPc: `63BFA50EE6523365FF14C1F45F88737B`
- APN/DNN: `internet`

Profile B :
- IMSI: `001010123456780`
- Ki: `00112233445566778899aabbccddeeff`
- OPc: `63BFA50EE6523365FF14C1F45F88737D`
- APN/DNN: `srsapn`

If any one of these differs, attach can fail (often as authentication reject or no PDU session).

---

## 3 Pre-checks

From workspace root:

```bash
cd "$WS"

# Binaries
test -x open5gs/build/tests/app/5gc && echo "open5gs ok"
test -x srsRAN_Project/build/apps/gnb/gnb && echo "gnb ok"
test -x srsRAN_4G/build/srsue/src/srsue && echo "srsue ok"

# MongoDB
systemctl is-active mongod

```

Ensure core config identity is aligned for open5gs, gnb, and UE.

Files changed in open5gs: `open5gs/build/configs/sample.yaml`, `/open5gs/configs/open5gs/amf.yaml.in`, `open5gs/configs/open5gs/nrf.yaml.in`


Required values:
- `mcc: 001`
- `mnc: 01`
- `tac: 7` (AMF + relevant TAI blocks)

### 3.1 Referenced Documentation

Open5gs : https://open5gs.org/open5gs/docs/guide/02-building-open5gs-from-sources/
O-RAN NearRT-RIC and xApp : https://docs.srsran.com/projects/project/en/latest/tutorials/source/near-rt-ric/source/index.html
srsRAN 4G (for UE) : https://docs.srsran.com/projects/4g/en/latest/general/source/1_installation.html and https://docs.srsran.com/projects/4g/en/latest/app_notes/source/zeromq/source/index.html


### 3.2 Recommended Host Packages (Ubuntu)

Install standard development and networking tools from the default Ubuntu repositories:

```bash
sudo apt update
sudo apt install -y git curl wget cmake make gcc g++ pkg-config meson ninja-build \
  libsctp-dev lksctp-tools libyaml-cpp-dev libzmq3-dev libfftw3-dev libmbedtls-dev \
  libgmp-dev libusb-1.0-0-dev iproute2 iptables gnupg lsb-release ca-certificates

# ```bash
# Additional recommended packages (Ubuntu)
sudo apt update
sudo apt install -y python3-pip python3-setuptools python3-wheel ninja-build build-essential \
  flex bison git cmake libsctp-dev libgnutls28-dev libgcrypt-dev libssl-dev \
  libmongoc-dev libbson-dev libyaml-dev libnghttp2-dev libmicrohttpd-dev \
  libcurl4-gnutls-dev libtins-dev libtalloc-dev libzmq3-dev libfftw3-dev \
  libmbedtls-dev libboost-program-options-dev libconfig++-dev libconfig++1v5 \
  libsctp-dev meson
```

### 3.3 Install Docker & Docker Compose v2

Because `docker-compose-plugin` is often missing or outdated in default repositories, install it via the official Docker repository:

```bash
# Add Docker's official GPG key and repo
sudo install -m 0755 -d /etc/apt/keyrings
sudo curl -fsSL [https://download.docker.com/linux/ubuntu/gpg](https://download.docker.com/linux/ubuntu/gpg) -o /etc/apt/keyrings/docker.asc
sudo chmod a+r /etc/apt/keyrings/docker.asc

echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] [https://download.docker.com/linux/ubuntu](https://download.docker.com/linux/ubuntu) \
  $(. /etc/os-release && echo "$VERSION_CODENAME") stable" | \
  sudo tee /etc/apt/sources.list.d/docker.list > /dev/null

sudo apt update
sudo apt install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin

```

### 3.4 Install MongoDB Clients

The `mongodb-clients` package has been removed from newer Ubuntu default repositories. Add the official MongoDB repository to install the necessary tools:

```bash
# Add MongoDB official GPG key and repo (Configured for MongoDB 7.0)
curl -fsSL [https://www.mongodb.org/static/pgp/server-7.0.asc](https://www.mongodb.org/static/pgp/server-7.0.asc) | \
   sudo gpg --yes --dearmor -o /usr/share/keyrings/mongodb-server-7.0.gpg

echo "deb [ arch=amd64,arm64 signed-by=/usr/share/keyrings/mongodb-server-7.0.gpg ] [https://repo.mongodb.org/apt/ubuntu](https://repo.mongodb.org/apt/ubuntu) \
  $(. /etc/os-release && echo "$VERSION_CODENAME")/mongodb-org/7.0 multiverse" | \
  sudo tee /etc/apt/sources.list.d/mongodb-org-7.0.list

sudo apt update
sudo apt install -y mongodb-org-tools mongodb-mongosh

```

### 3.5 Docker Prerequisites

Ensure the Docker daemon is running and your user has the correct permissions:

```bash
sudo systemctl enable --now docker
sudo usermod -aG docker "$USER"
# IMPORTANT: Log out and log back in (or close/reopen your terminal) to apply group updates.

```

---

## 4) Open5GS Core Setup (Source Build)

### 4.1 Build/update

```bash
cd "$OPEN5GS_ROOT"
git pull --ff-only
meson setup build --prefix="$(pwd)/install" --reconfigure
ninja -C build
```

### 4.2 Ensure core config identity is aligned

File: `open5gs/build/configs/sample.yaml`

Required values:
- `mcc: 001`
- `mnc: 01`
- `tac: 7` (AMF + relevant TAI blocks)

### 4.3 Bring up tunnel + forwarding (sudo)

```bash
sudo ip tuntap add name "$OGS_TUN" mode tun || true
sudo ip addr add "$CORE_GW_IP"/16 dev "$OGS_TUN" || true
sudo ip link set "$OGS_TUN" up
sudo iptables -I FORWARD -i "$OGS_TUN" -j ACCEPT
sudo iptables -I FORWARD -o "$OGS_TUN" -j ACCEPT
```

### 4.4 Start core

```bash
cd "$OPEN5GS_ROOT"
./build/tests/app/5gc
```

If restarting often, avoid duplicate instances:

```bash
sudo pkill -f 'open5gs-(nrfd|scpd|amfd|smfd|upfd|ausfd|udmd|udrd|pcfd|nssfd|bsfd)' || true
sudo pkill -f '/open5gs/build/tests/app/5gc' || true
```

### 4.4.1 Crash-safe test runner (recommended after host crash)

When the host crashes, Open5GS tests can fail repeatedly due to stale sockets or
UPF failing to open `/dev/net/tun` without privileges. Use the helper below to
force cleanup, re-create `ogstun`, and run tests with `sudo`:

```bash
cd "$WS"
./groupStudies/scripts/open5gs_recover_and_test.sh "$OPEN5GS_ROOT"
```

Run one test only (example):

```bash
cd "$WS"
./groupStudies/scripts/open5gs_recover_and_test.sh "$OPEN5GS_ROOT" registration
```

### 4.5 Provision subscriber in MongoDB

```bash
DBCTL="$OPEN5GS_ROOT/build/misc/db/open5gs-dbctl"

bash "$DBCTL" remove "$IMSI" || true
bash "$DBCTL" add_ue_with_apn \
  "$IMSI" \
  "$KI" \
  "$OPC" \
  "$APN"

bash "$DBCTL" showpretty | sed -n "/$IMSI/,+40p"
```


```bash
DBCTL="$OPEN5GS_ROOT/build/misc/db/open5gs-dbctl"
bash "$DBCTL" remove 001010123456780 || true
bash "$DBCTL" add_ue_with_apn \
  001010123456780 \
  00112233445566778899aabbccddeeff \
  63BFA50EE6523365FF14C1F45F88737D \
  srsapn

bash "$DBCTL" showfiltered
```

### 4.6 Optional: Open5GS WebUI

```bash
cd "$OPEN5GS_ROOT/webui"
npm ci
npm run build
npm run dev
```

Open WebUI and verify subscriber fields:
Profile A :
- IMSI: `001010000000101`
- Ki: `0C0A34601D4F07677303652C0462535B`
- OPc: `63BFA50EE6523365FF14C1F45F88737B`
- APN/DNN: `internet`

Profile B :
- IMSI: `001010123456780`
- Ki: `00112233445566778899aabbccddeeff`
- OPc: `63BFA50EE6523365FF14C1F45F88737D`
- APN/DNN: `srsapn`

---

## 5) O-RAN SC Near-RT RIC Setup

### 5.1 Repo

```bash
cd "$RIC_ROOT"
git pull --ff-only
```

### 5.2 Compose file replacement

If your guideline provides a custom compose, replace:
- `oran-sc-ric/docker-compose.yml`

### 5.3 Start RIC

```bash
docker compose -f "$RIC_ROOT/docker-compose.yml" \
  --project-directory "$RIC_ROOT" up -d
```

Check status:

```bash
docker compose -f "$RIC_ROOT/docker-compose.yml" \
  --project-directory "$RIC_ROOT" ps
```

Expected up containers include: `ric_dbaas`, `ric_e2term`, `ric_e2mgr`, `ric_submgr`, `ric_appmgr`, `ric_rtmgr_sim`.

---

## 6) gNB + srsUE (ZMQ)

### 6.1 gNB config (`gnb_zmq.yaml`) required values

- `cu_cp.amf.addr: 127.0.0.5`
- `cu_cp.amf.port: 38412`
- `cu_cp.amf.bind_addr: 127.0.0.1`
- Tracking area + cell:
  - `plmn: "00101"`
  - `tac: 7`
- `e2.addr: 127.0.0.1` in YAML is ignored when CLI `e2 --addr ...` is passed
- Use runtime-discovered RIC `e2term` container IP (recommended after interface changes)

### 6.2 UE config (`ue_zmq.conf`) required values

- `[usim]` must match one DB profile exactly
- `[nas] apn` must match that same profile exactly (`internet` or `srsapn`)
- `[gw] netns = ue1`

Validated status (2026-03-09): UE reaches `Attaching UE...` with aligned DB/UE profile.

### 6.3 Start order (important)

Terminal 1 (gNB):

```bash
sudo pkill -9 -f srsue || true
sudo pkill -9 -f '/build/apps/gnb/gnb' || true

# Ensure RIC E2 services are up
cd "$RIC_ROOT"
docker compose up -d e2term e2mgr rtmgr_sim

# Discover current E2TERM IP on docker bridge (changes across restarts/interface updates)
E2_IP=$(docker inspect -f '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}' ric_e2term)
echo "Using E2_IP=$E2_IP"

sudo "$GNB_ROOT/build/apps/gnb/gnb" \
  -c "groupStudies/gnb_zmq.yaml" \
  log --all_level=info --e2ap_level=debug --ngap_level=info \
  e2 --enable_du_e2=true --enable_cu_cp_e2=true --enable_cu_up_e2=true \
     --e2sm_kpm_enabled=true --e2sm_rc_enabled=true \
     --addr="$E2_IP" --port=36421 --bind_addr="$GNB_BIND_ADDR"
```

Validated note (2026-03-09): in this workspace, RIC SCTP association was established only after explicitly enabling all E2 agents (`DU`, `CU-CP`, `CU-UP`) on the gNB CLI.

Terminal 2 (UE):

```bash
sudo ip netns del "$UE_NS" 2>/dev/null || true
sudo ip netns add "$UE_NS"

sudo "./$UE_ROOT/build/srsue/src/srsue" \
  "./groupStudies/ue_zmq.conf"
```

Expected UE lines:
- `Random Access Complete`
- `RRC Connected`
- `PDU Session Establishment successful. IP: ...`

---

## 7) End-to-End Validation (UE to Core + Internet)

Terminal 3:

```bash
sudo ip netns exec "$UE_NS" ip -brief addr
sudo ip netns exec "$UE_NS" ip route
sudo ip netns exec "$UE_NS" ping -c 5 "$CORE_GW_IP"
```

Expected:
- `tun_srsue` has IP (e.g. `10.45.0.x`)
- Route via `tun_srsue`
- Ping `0% packet loss`

### 7.1 Internet reachability test (per srsRAN Near-RT RIC tutorial troubleshooting)

If UE-to-core ping works but Internet ping fails, apply host forwarding/NAT and retest from `ue1`:

```bash
# Ensure UE has default route
sudo ip netns exec "$UE_NS" ip route add default via "$CORE_GW_IP" dev tun_srsue 2>/dev/null || true

# Enable forwarding and NAT on host egress interface
OUT_IFACE=$(ip route show default | awk '{print $5; exit}')
sudo sysctl -w net.ipv4.ip_forward=1
sudo iptables -t nat -C POSTROUTING -o "$OUT_IFACE" -j MASQUERADE 2>/dev/null || \
  sudo iptables -t nat -A POSTROUTING -o "$OUT_IFACE" -j MASQUERADE

# Required when host FORWARD policy is DROP (common with Kubernetes/docker hosts)
sudo iptables -C FORWARD -i "$OGS_TUN" -o "$OUT_IFACE" -j ACCEPT 2>/dev/null || \
  sudo iptables -I FORWARD 1 -i "$OGS_TUN" -o "$OUT_IFACE" -j ACCEPT
sudo iptables -C FORWARD -i "$OUT_IFACE" -o "$OGS_TUN" -m conntrack --ctstate RELATED,ESTABLISHED -j ACCEPT 2>/dev/null || \
  sudo iptables -I FORWARD 1 -i "$OUT_IFACE" -o "$OGS_TUN" -m conntrack --ctstate RELATED,ESTABLISHED -j ACCEPT

# Internet test from UE namespace
if command -v traceroute >/dev/null 2>&1; then
  sudo ip netns exec "$UE_NS" traceroute -n -m 5 8.8.8.8
else
  sudo ip netns exec "$UE_NS" tracepath -n 8.8.8.8
fi
sudo ip netns exec "$UE_NS" ping -c 5 8.8.8.8
```

Expected:
- First hop is `10.45.0.1` (Open5GS `ogstun` gateway) in traceroute/tracepath output
- `8.8.8.8` ping from `ue1` returns `0% packet loss`.

---

## 8) Build Fallback Commands

### 8.1 srsRAN_Project build fallback

```bash
cd "$WS"
git clone https://github.com/srsran/srsRAN_Project.git
cd srsRAN_Project
mkdir build && cd build
cmake ../ -DENABLE_EXPORT=ON -DENABLE_ZEROMQ=ON -DAUTO_DETECT_ISA=OFF
make -j"$(nproc)"
```

### 8.2 srsRAN_4G (Ubuntu 24.04) fallback

```bash
sudo apt install -y gcc-11 g++-11
cd "$WS"
git clone https://github.com/srsRAN/srsRAN_4G.git
cd srsRAN_4G
mkdir build && cd build
export CC="$(which gcc-11)"
export CXX="$(which g++-11)"
cmake ../ -B build
cmake --build build -j"$(nproc)"
```

---

## 10) KPIMON xApp (Near-RT RIC)

Reference integration path follows srsRAN Near-RT RIC tutorial.

From `oran-sc-ric`:

```bash
cd "$RIC_ROOT"
docker compose up -d
docker compose exec python_xapp_runner ./kpm_mon_xapp.py
```

If xApp code or Dockerfile dependencies change:

```bash
docker compose build --no-cache python_xapp_runner
docker compose up -d python_xapp_runner
docker compose exec python_xapp_runner ./kpm_mon_xapp.py
```

---

## 11) Recommended Startup / Shutdown Order

### Startup
1. MongoDB
2. Open5GS (`5gc`)
3. RIC (`docker compose up`)
4. gNB
5. UE
6. KPI xApp (optional)

### Shutdown
1. UE
2. gNB
3. xApp containers (optional)
4. RIC stack
5. Open5GS

This order prevents stale sockets/sessions and reduces `Address already in use` errors.

---

## 12) Common Problems and Fixes

### A) `Attaching UE...` appears stuck

**Cause**:
- gNB/UE stale state
- ZMQ ports already in use
- UE netns not recreated

**Fix**:
```bash
sudo pkill -9 -f srsue || true
sudo pkill -9 -f '/build/apps/gnb/gnb' || true
sudo ip netns del ue1 2>/dev/null || true
sudo ip netns add ue1
```
Then start gNB first, UE second.

### B) `Authentication Reject`

**Cause**:
- IMSI/K/OPc/APN mismatch between UE and Open5GS DB
- PLMN/TAC mismatch across gNB and core

**Fix**:
- Re-provision subscriber with `open5gs-dbctl`
- Verify PLMN `00101`, TAC `7`, APN `internet` everywhere

### C) `N2: Failed to connect to AMF`

**Cause**:
- gNB points to wrong AMF address

**Fix**:
- Set `gnb_zmq.yaml`: `amf.addr=127.0.0.5`, `port=38412`, `bind_addr=127.0.0.1`

### D) `Failed to setup/configure GW interface`

**Cause**:
- Running srsUE without `sudo`

**Fix**:
- Run srsUE with `sudo`

### E) `Address already in use` on ZMQ 2000/2001

**Cause**:
- Old UE/gNB still running

**Fix**:
```bash
sudo pkill -9 -f srsue || true
sudo pkill -9 -f '/build/apps/gnb/gnb' || true
ss -ltnp | grep -E ':2000|:2001' || true
```

### F) `/tmp/*.log permission denied`

**Cause**:
- Current user cannot write/append in target path

**Fix**:
- Use writable log paths in workspace (already applied in this setup)

### G) Ping fails but UE attached

Check:
```bash
sudo ip netns exec "$UE_NS" ip -brief addr
sudo ip netns exec "$UE_NS" ip route
ip -brief addr show "$OGS_TUN"
sudo iptables -S FORWARD | grep "$OGS_TUN"
```

If this still fails, run the full Internet troubleshooting sequence:

```bash
OUT_IFACE=$(ip route show default | awk '{print $5; exit}')
sudo sysctl -w net.ipv4.ip_forward=1
sudo iptables -t nat -C POSTROUTING -o "$OUT_IFACE" -j MASQUERADE 2>/dev/null || \
  sudo iptables -t nat -A POSTROUTING -o "$OUT_IFACE" -j MASQUERADE
sudo iptables -C FORWARD -i "$OGS_TUN" -o "$OUT_IFACE" -j ACCEPT 2>/dev/null || \
  sudo iptables -I FORWARD 1 -i "$OGS_TUN" -o "$OUT_IFACE" -j ACCEPT
sudo iptables -C FORWARD -i "$OUT_IFACE" -o "$OGS_TUN" -m conntrack --ctstate RELATED,ESTABLISHED -j ACCEPT 2>/dev/null || \
  sudo iptables -I FORWARD 1 -i "$OUT_IFACE" -o "$OGS_TUN" -m conntrack --ctstate RELATED,ESTABLISHED -j ACCEPT
sudo ip netns exec "$UE_NS" ping -c 5 8.8.8.8
```

In this workspace, root cause was host `FORWARD` policy set to `DROP`; explicit `ogstun <-> $OUT_IFACE` rules solved it.

Also verify there is no route conflict caused by a second Open5GS instance:

```bash
ip route | grep -E '^10.45.0.0/16'
```

If route points to an unintended interface/service, disable the conflicting instance or bring `ogstun` down in that other setup.

### H) UE attaches then drops quickly

**Cause**:
- gNB/UE restarted out-of-order
- stale `ue1` namespace from older session

**Fix**:
```bash
sudo ip netns del ue1 2>/dev/null || true
sudo ip netns add ue1
```
Restart gNB first, then UE.

### I) RIC E2 connect fails

**Cause**:
- RIC containers not healthy
- gNB E2 endpoint (`--addr`) mismatched to container network

**Fix**:
```bash
docker compose -f "$RIC_ROOT/docker-compose.yml" \
  --project-directory "$RIC_ROOT" ps
```
Then verify `gNB` uses reachable RIC address/port (`36421`).

If NG setup works but E2 does not, force-enable all E2 agents and service models on gNB launch:

```bash
sudo "$GNB_ROOT/build/apps/gnb/gnb" \
  -c "$GNB_ROOT/build/apps/gnb/gnb_zmq.yaml" \
  log --all_level=info --e2ap_level=debug --ngap_level=info \
  e2 --enable_du_e2=true --enable_cu_cp_e2=true --enable_cu_up_e2=true \
     --e2sm_kpm_enabled=true --e2sm_rc_enabled=true \
     --addr="$E2_IP" --port=36421 --bind_addr="$GNB_BIND_ADDR"
```

Quick confirmation checks:

```bash
# gNB log should show accepted E2 connections
grep -E 'E2 connection to Near-RT-RIC on .* accepted|E2-CU-CP|E2-CU-UP|E2-DU' /tmp/gnb.log

# e2term container should show SCTP associations
docker exec ric_e2term sh -c 'cat /proc/net/sctp/assocs'
```

If VM interfaces changed or docker networks were recreated, do not hardcode `10.0.2.10`.
Use:

```bash
docker inspect -f '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}' ric_e2term
```

Then pass that value in gNB launch: `e2 --addr="<ric_e2term_ip>" --bind_addr="10.0.2.1"`.

### J) `Unknown UE by SUCI` appears in AMF log

**Explanation**:
- This can be a normal first step before identity resolution.

**Action**:
- Not fatal if followed by `Registration complete` and session creation logs.

### K) Loopback alias missing for older configs (10.53.1.x)

Only needed if your gNB config still references `10.53.1.1/10.53.1.2`.

```bash
sudo ip addr add 10.53.1.1/24 dev lo || true
sudo ip addr add 10.53.1.2/24 dev lo || true
```

### L) UE exits after `Attaching UE...` with `Closing stdin thread.`

**Cause**:
- srsUE launched from a non-interactive shell/session where stdin closes early
- detached automation run (`nohup`/non-tty runner) in some environments

**Fix**:
- Run srsUE in a persistent interactive terminal (or `tmux`/`screen`) and keep it in foreground.
- Keep startup order strict: Open5GS -> RIC -> gNB -> UE.

```bash
sudo ip netns del "$UE_NS" 2>/dev/null || true
sudo ip netns add "$UE_NS"
sudo "$UE_ROOT/build/srsue/src/srsue" \
  "$WS/ue_zmq.conf"
```

Expected progression in UE terminal:
- `Random Access Complete`
- `RRC Connected`
- `PDU Session Establishment successful`

---

## 13) Log Locations and High-Value Greps

- gNB: `/tmp/gnb.log`
- UE: `/tmp/ue.log`
- Open5GS runtime: `/tmp/open5gs_5gc.out`

Useful checks:

```bash
grep -E 'Registration complete|AMF-Sessions|SMF-Sessions|UPF-Sessions|reject|ERROR|FATAL' /tmp/open5gs_5gc.out | tail -n 50
grep -E 'N2: Connection to AMF|Connected to AMF|PDUSessionResourceSetup|ERROR' /tmp/gnb.log | tail -n 80
grep -E 'Attaching UE|RRC Connected|PDU Session Establishment successful|Reject|Failed' /tmp/ue.log | tail -n 80
```

---

## 16) Quick Daily Re-Run (Minimal Commands)

```bash
# Terminal A
cd "$OPEN5GS_ROOT"
./build/tests/app/5gc

# Terminal B
cd "$RIC_ROOT"
docker compose up -d

# Terminal C
cd "$GNB_ROOT/build/apps/gnb"
E2_IP=$(docker inspect -f '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}' ric_e2term)
sudo ./gnb -c gnb_zmq.yaml \
  log --all_level=info --e2ap_level=debug --ngap_level=info \
  e2 --enable_du_e2=true --enable_cu_cp_e2=true --enable_cu_up_e2=true \
     --e2sm_kpm_enabled=true --e2sm_rc_enabled=true \
     --addr="$E2_IP" --port=36421 --bind_addr="$GNB_BIND_ADDR"

# Terminal D
sudo ip netns del "$UE_NS" 2>/dev/null || true
sudo ip netns add "$UE_NS"
sudo "$UE_ROOT/build/srsue/src/srsue" \
  "$WS/ue_zmq.conf"

# Terminal E
sudo ip netns exec "$UE_NS" ping -c 5 "$CORE_GW_IP"

# Terminal F (optional Internet validation)
OUT_IFACE=$(ip route show default | awk '{print $5; exit}')
sudo sysctl -w net.ipv4.ip_forward=1
sudo iptables -t nat -C POSTROUTING -o "$OUT_IFACE" -j MASQUERADE 2>/dev/null || sudo iptables -t nat -A POSTROUTING -o "$OUT_IFACE" -j MASQUERADE
sudo iptables -C FORWARD -i "$OGS_TUN" -o "$OUT_IFACE" -j ACCEPT 2>/dev/null || sudo iptables -I FORWARD 1 -i "$OGS_TUN" -o "$OUT_IFACE" -j ACCEPT
sudo iptables -C FORWARD -i "$OUT_IFACE" -o "$OGS_TUN" -m conntrack --ctstate RELATED,ESTABLISHED -j ACCEPT 2>/dev/null || sudo iptables -I FORWARD 1 -i "$OUT_IFACE" -o "$OGS_TUN" -m conntrack --ctstate RELATED,ESTABLISHED -j ACCEPT
if command -v traceroute >/dev/null 2>&1; then
  sudo ip netns exec "$UE_NS" traceroute -n -m 5 8.8.8.8
else
  sudo ip netns exec "$UE_NS" tracepath -n 8.8.8.8
fi
sudo ip netns exec "$UE_NS" ping -c 5 8.8.8.8
```
