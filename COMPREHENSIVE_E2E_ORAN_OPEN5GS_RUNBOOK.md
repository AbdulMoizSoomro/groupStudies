# Comprehensive E2E O-RAN + Open5GS Runbook (Validated in This Workspace)

Date: 2026-02-13  
Workspace: `/home/abdul-moiz-soomro/prj/group_studies`

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

## 2) Known Good Identity/Session Values

Use these values consistently across Open5GS, gNB, UE, and subscriber DB:

- PLMN: `00101`
- TAC: `7`
- IMSI: `001010000000101`
- Ki: `0C0A34601D4F07677303652C0462535B`
- OPc: `63BFA50EE6523365FF14C1F45F88737B`
- APN/DNN: `internet`

If any one of these differs, attach can fail (often as authentication reject or no PDU session).

---

## 3) Pre-checks

From workspace root:

```bash
cd /home/abdul-moiz-soomro/prj/group_studies

# Binaries
test -x open5gs/build/tests/app/5gc && echo "open5gs ok"
test -x srsRAN_Project/build/apps/gnb/gnb && echo "gnb ok"
test -x srsRAN_4G/build/srsue/src/srsue && echo "srsue ok"

# MongoDB
systemctl is-active mongod
```

Recommended host packages (Ubuntu):

```bash
sudo apt update
sudo apt install -y git curl wget cmake make gcc g++ pkg-config meson ninja-build \
  libsctp-dev lksctp-tools libyaml-cpp-dev libzmq3-dev libfftw3-dev libmbedtls-dev \
  libgmp-dev libusb-1.0-0-dev mongodb-clients iproute2 iptables docker.io docker-compose-plugin
```

Docker prerequisites:

```bash
sudo systemctl enable --now docker
sudo usermod -aG docker "$USER"
# log out/in once after group update
```

---

## 4) Open5GS Core Setup (Source Build)

Reference: https://open5gs.org/open5gs/docs/guide/02-building-open5gs-from-sources/

### 4.1 Build/update

```bash
cd /home/abdul-moiz-soomro/prj/group_studies/open5gs
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
sudo ip tuntap add name ogstun mode tun || true
sudo ip addr add 10.45.0.1/16 dev ogstun || true
sudo ip link set ogstun up
sudo iptables -I FORWARD -i ogstun -j ACCEPT
sudo iptables -I FORWARD -o ogstun -j ACCEPT
```

### 4.4 Start core

```bash
cd /home/abdul-moiz-soomro/prj/group_studies/open5gs
./build/tests/app/5gc
```

If restarting often, avoid duplicate instances:

```bash
pkill -f 'open5gs-(nrfd|scpd|amfd|smfd|upfd|ausfd|udmd|udrd|pcfd|nssfd|bsfd)' || true
pkill -f '/open5gs/build/tests/app/5gc' || true
```

### 4.5 Provision subscriber in MongoDB

```bash
DBCTL=/home/abdul-moiz-soomro/prj/group_studies/open5gs/build/misc/db/open5gs-dbctl

bash "$DBCTL" remove 001010000000101 || true
bash "$DBCTL" add_ue_with_apn \
  001010000000101 \
  0C0A34601D4F07677303652C0462535B \
  63BFA50EE6523365FF14C1F45F88737B \
  internet

bash "$DBCTL" showpretty | sed -n '/001010000000101/,+40p'
```

### 4.6 Optional: Open5GS WebUI

```bash
cd /home/abdul-moiz-soomro/prj/group_studies/open5gs/webui
npm ci
npm run build
npm run dev
```

Open WebUI and verify subscriber fields:
- IMSI `001010000000101`
- Ki `0C0A34601D4F07677303652C0462535B`
- OPc `63BFA50EE6523365FF14C1F45F88737B`
- APN/DNN `internet`

---

## 5) O-RAN SC Near-RT RIC Setup

### 5.1 Repo

```bash
cd /home/abdul-moiz-soomro/prj/group_studies/oran-sc-ric
git pull --ff-only
```

### 5.2 Compose file replacement

If your guideline provides a custom compose, replace:
- `oran-sc-ric/docker-compose.yml`

### 5.3 Start RIC

```bash
docker compose -f /home/abdul-moiz-soomro/prj/group_studies/oran-sc-ric/docker-compose.yml \
  --project-directory /home/abdul-moiz-soomro/prj/group_studies/oran-sc-ric up -d 
```

Check status:

```bash
docker compose -f /home/abdul-moiz-soomro/prj/group_studies/oran-sc-ric/docker-compose.yml \
  --project-directory /home/abdul-moiz-soomro/prj/group_studies/oran-sc-ric ps
```

Expected up containers include: `ric_dbaas`, `ric_e2term`, `ric_e2mgr`, `ric_submgr`, `ric_appmgr`, `ric_rtmgr_sim`.

---

## 6) gNB + srsUE (Option 1, ZMQ)

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

- `[usim]`
  - `imsi = 001010000000101`
  - `k = 0C0A34601D4F07677303652C0462535B`
  - `opc = 63BFA50EE6523365FF14C1F45F88737B`
- `[nas] apn = internet`
- `[gw] netns = ue1`

### 6.3 Start order (important)

Terminal 1 (gNB):

```bash
sudo pkill -9 -f srsue || true
sudo pkill -9 -f '/build/apps/gnb/gnb' || true

# Ensure RIC E2 services are up
cd /home/abdul-moiz-soomro/prj/group_studies/oran-sc-ric
docker compose up -d e2term e2mgr rtmgr_sim

# Discover current E2TERM IP on docker bridge (changes across restarts/interface updates)
E2_IP=$(docker inspect -f '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}' ric_e2term)
echo "Using E2_IP=$E2_IP"

sudo /home/abdul-moiz-soomro/prj/group_studies/srsRAN_Project/build/apps/gnb/gnb \
  -c /home/abdul-moiz-soomro/prj/group_studies/srsRAN_Project/build/apps/gnb/gnb_zmq.yaml \
  e2 --addr="$E2_IP" --bind_addr="10.0.2.1"
```

Terminal 2 (UE):

```bash
sudo ip netns del ue1 2>/dev/null || true
sudo ip netns add ue1

sudo /home/abdul-moiz-soomro/prj/group_studies/srsRAN_4G/build/srsue/src/srsue \
  /home/abdul-moiz-soomro/prj/group_studies/srsRAN_4G/build/build/srsue/src/ue_zmq.conf
```

Expected UE lines:
- `Random Access Complete`
- `RRC Connected`
- `PDU Session Establishment successful. IP: ...`

---

## 7) End-to-End Validation (UE to Core + Internet)

Terminal 3:

```bash
sudo ip netns exec ue1 ip -brief addr
sudo ip netns exec ue1 ip route
sudo ip netns exec ue1 ping -c 5 10.45.0.1
```

Expected:
- `tun_srsue` has IP (e.g. `10.45.0.x`)
- Route via `tun_srsue`
- Ping `0% packet loss`

### 7.1 Internet reachability test (per srsRAN Near-RT RIC tutorial troubleshooting)

If UE-to-core ping works but Internet ping fails, apply host forwarding/NAT and retest from `ue1`:

```bash
# Ensure UE has default route
sudo ip netns exec ue1 ip route add default via 10.45.0.1 dev tun_srsue 2>/dev/null || true

# Enable forwarding and NAT on host egress interface
OUT_IFACE=$(ip route show default | awk '{print $5; exit}')
sudo sysctl -w net.ipv4.ip_forward=1
sudo iptables -t nat -C POSTROUTING -o "$OUT_IFACE" -j MASQUERADE 2>/dev/null || \
  sudo iptables -t nat -A POSTROUTING -o "$OUT_IFACE" -j MASQUERADE

# Required when host FORWARD policy is DROP (common with Kubernetes/docker hosts)
sudo iptables -C FORWARD -i ogstun -o "$OUT_IFACE" -j ACCEPT 2>/dev/null || \
  sudo iptables -I FORWARD 1 -i ogstun -o "$OUT_IFACE" -j ACCEPT
sudo iptables -C FORWARD -i "$OUT_IFACE" -o ogstun -m conntrack --ctstate RELATED,ESTABLISHED -j ACCEPT 2>/dev/null || \
  sudo iptables -I FORWARD 1 -i "$OUT_IFACE" -o ogstun -m conntrack --ctstate RELATED,ESTABLISHED -j ACCEPT

# Internet test from UE namespace
if command -v traceroute >/dev/null 2>&1; then
  sudo ip netns exec ue1 traceroute -n -m 5 8.8.8.8
else
  sudo ip netns exec ue1 tracepath -n 8.8.8.8
fi
sudo ip netns exec ue1 ping -c 5 8.8.8.8
```

Expected:
- First hop is `10.45.0.1` (Open5GS `ogstun` gateway) in traceroute/tracepath output
- `8.8.8.8` ping from `ue1` returns `0% packet loss`.

---

## 8) Build Fallback Commands

### 8.1 srsRAN_Project build fallback

```bash
cd /home/abdul-moiz-soomro/prj/group_studies
git clone https://github.com/srsran/srsRAN_Project.git
cd srsRAN_Project
mkdir build && cd build
cmake ../ -DENABLE_EXPORT=ON -DENABLE_ZEROMQ=ON -DAUTO_DETECT_ISA=OFF
make -j"$(nproc)"
```

### 8.2 srsRAN_4G (Ubuntu 24.04) fallback

```bash
sudo apt install -y gcc-11 g++-11
cd /home/abdul-moiz-soomro/prj/group_studies
git clone https://github.com/srsRAN/srsRAN_4G.git
cd srsRAN_4G
mkdir build && cd build
export CC="$(which gcc-11)"
export CXX="$(which g++-11)"
cmake ../ -B build
cmake --build build -j"$(nproc)"
```

---

## 9) Option 2 (USRP + COTS UE)

Use this only when moving from ZMQ emulation to RF hardware.

### 9.1 UHD + RF dependencies

```bash
sudo apt-get install -y libuhd-dev uhd-host
sudo uhd_images_downloader
uhd_find_devices

sudo apt-get install -y \
  libpcsclite-dev libbladerf-dev soapysdr-module-all libsctp-dev doxygen \
  libdwarf-dev libelf-dev binutils-dev libdw-dev libmbedtls-dev libyaml-cpp-dev \
  lksctp-tools libconfig++-dev
```

### 9.2 gNB RF config

Use hardware-specific config (for example):
- `srsRAN_Project/configs/gnb_rf_b200_tdd_n78_20mhz.yml`

Run:

```bash
cd /home/abdul-moiz-soomro/prj/group_studies/srsRAN_Project/build/apps/gnb
sudo ./gnb -c gnb_rf_b200_tdd_n78_20mhz.yml e2 --addr="10.0.2.10" --bind_addr="10.0.2.1"
```

### 9.3 COTS UE checks

- SIM uses PLMN `00101`
- APN `internet`
- Device supports private PLMN
- Confirm UE receives IP from `10.45.0.0/16` pool and passes UL/DL traffic

---

## 10) KPIMON xApp (Near-RT RIC)

Reference integration path follows srsRAN Near-RT RIC tutorial.

From `oran-sc-ric`:

```bash
cd /home/abdul-moiz-soomro/prj/group_studies/oran-sc-ric
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
sudo ip netns exec ue1 ip -brief addr
sudo ip netns exec ue1 ip route
ip -brief addr show ogstun
sudo iptables -S FORWARD | grep ogstun
```

If this still fails, run the full Internet troubleshooting sequence:

```bash
OUT_IFACE=$(ip route show default | awk '{print $5; exit}')
sudo sysctl -w net.ipv4.ip_forward=1
sudo iptables -t nat -C POSTROUTING -o "$OUT_IFACE" -j MASQUERADE 2>/dev/null || \
  sudo iptables -t nat -A POSTROUTING -o "$OUT_IFACE" -j MASQUERADE
sudo iptables -C FORWARD -i ogstun -o "$OUT_IFACE" -j ACCEPT 2>/dev/null || \
  sudo iptables -I FORWARD 1 -i ogstun -o "$OUT_IFACE" -j ACCEPT
sudo iptables -C FORWARD -i "$OUT_IFACE" -o ogstun -m conntrack --ctstate RELATED,ESTABLISHED -j ACCEPT 2>/dev/null || \
  sudo iptables -I FORWARD 1 -i "$OUT_IFACE" -o ogstun -m conntrack --ctstate RELATED,ESTABLISHED -j ACCEPT
sudo ip netns exec ue1 ping -c 5 8.8.8.8
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
docker compose -f /home/abdul-moiz-soomro/prj/group_studies/oran-sc-ric/docker-compose.yml \
  --project-directory /home/abdul-moiz-soomro/prj/group_studies/oran-sc-ric ps
```
Then verify `gNB` uses reachable RIC address/port (`36421`).

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

---

## 13) Log Locations and High-Value Greps

- gNB: `/home/abdul-moiz-soomro/prj/group_studies/gnb.log`
- UE: `/home/abdul-moiz-soomro/prj/group_studies/ue.log`
- Open5GS runtime: `/tmp/open5gs_5gc.out`

Useful checks:

```bash
grep -E 'Registration complete|AMF-Sessions|SMF-Sessions|UPF-Sessions|reject|ERROR|FATAL' /tmp/open5gs_5gc.out | tail -n 50
grep -E 'N2: Connection to AMF|Connected to AMF|PDUSessionResourceSetup|ERROR' /home/abdul-moiz-soomro/prj/group_studies/gnb.log | tail -n 80
grep -E 'Attaching UE|RRC Connected|PDU Session Establishment successful|Reject|Failed' /home/abdul-moiz-soomro/prj/group_studies/ue.log | tail -n 80
```

---

## 14) Final Success Criteria

System is considered E2E healthy when all are true:

1. Open5GS NFs are running and no critical startup failures.
2. RIC compose services are up.
3. gNB shows AMF and E2 connections established.
4. UE shows `PDU Session Establishment successful`.
5. `sudo ip netns exec ue1 ping -c 5 10.45.0.1` returns `0% packet loss`.
6. Traceroute path check (`traceroute` if available, otherwise `tracepath`) shows first hop `10.45.0.1`.
7. `sudo ip netns exec ue1 ping -c 5 8.8.8.8` returns `0% packet loss`.

---

## 15) Notes About Privileges

- `sudo` is required for:
  - `ip netns` operations
  - srsUE GW setup
  - TUN/iptables setup
- Non-root runs may look partially healthy but fail at netns/GW stage.

---

## 16) Quick Daily Re-Run (Minimal Commands)

```bash
# Terminal A
cd /home/abdul-moiz-soomro/prj/group_studies/open5gs
./build/tests/app/5gc

# Terminal B
cd /home/abdul-moiz-soomro/prj/group_studies/oran-sc-ric
docker compose up -d

# Terminal C
cd /home/abdul-moiz-soomro/prj/group_studies/srsRAN_Project/build/apps/gnb
E2_IP=$(docker inspect -f '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}' ric_e2term)
sudo ./gnb -c gnb_zmq.yaml e2 --addr="$E2_IP" --bind_addr="10.0.2.1"

# Terminal D
sudo ip netns del ue1 2>/dev/null || true
sudo ip netns add ue1
sudo /home/abdul-moiz-soomro/prj/group_studies/srsRAN_4G/build/srsue/src/srsue \
  /home/abdul-moiz-soomro/prj/group_studies/srsRAN_4G/build/build/srsue/src/ue_zmq.conf

# Terminal E
sudo ip netns exec ue1 ping -c 5 10.45.0.1

# Terminal F (optional Internet validation)
OUT_IFACE=$(ip route show default | awk '{print $5; exit}')
sudo sysctl -w net.ipv4.ip_forward=1
sudo iptables -t nat -C POSTROUTING -o "$OUT_IFACE" -j MASQUERADE 2>/dev/null || sudo iptables -t nat -A POSTROUTING -o "$OUT_IFACE" -j MASQUERADE
sudo iptables -C FORWARD -i ogstun -o "$OUT_IFACE" -j ACCEPT 2>/dev/null || sudo iptables -I FORWARD 1 -i ogstun -o "$OUT_IFACE" -j ACCEPT
sudo iptables -C FORWARD -i "$OUT_IFACE" -o ogstun -m conntrack --ctstate RELATED,ESTABLISHED -j ACCEPT 2>/dev/null || sudo iptables -I FORWARD 1 -i "$OUT_IFACE" -o ogstun -m conntrack --ctstate RELATED,ESTABLISHED -j ACCEPT
if command -v traceroute >/dev/null 2>&1; then
  sudo ip netns exec ue1 traceroute -n -m 5 8.8.8.8
else
  sudo ip netns exec ue1 tracepath -n 8.8.8.8
fi
sudo ip netns exec ue1 ping -c 5 8.8.8.8
```
