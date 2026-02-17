# Comprehensive E2E O-RAN + Open5GS Runbook (Validated in This Workspace)

Date: 2026-02-13  
Workspace: `/home/abdul-moiz-soomro/prj/group_studies`

This document provides a full, practical, end-to-end setup for:
- Open5GS 5G Core (from source)
- O-RAN SC Near-RT RIC (docker compose)
- srsRAN gNB + srsUE using ZeroMQ (no RF hardware)
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
  --project-directory /home/abdul-moiz-soomro/prj/group_studies/oran-sc-ric up
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
- `e2.addr: 127.0.0.1` (RIC local path)
- Launch cmd E2 endpoint:
  - `e2 --addr="10.0.2.10" --bind_addr="10.0.2.1"`

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

/home/abdul-moiz-soomro/prj/group_studies/srsRAN_Project/build/apps/gnb/gnb \
  -c /home/abdul-moiz-soomro/prj/group_studies/srsRAN_Project/build/apps/gnb/gnb_zmq.yaml \
  e2 --addr="10.0.2.10" --bind_addr="10.0.2.1"
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

## 7) End-to-End Validation (UE to Core)

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

## 9) Common Problems and Fixes

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

---

## 10) Log Locations and High-Value Greps

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

## 11) Final Success Criteria

System is considered E2E healthy when all are true:

1. Open5GS NFs are running and no critical startup failures.
2. RIC compose services are up.
3. gNB shows AMF and E2 connections established.
4. UE shows `PDU Session Establishment successful`.
5. `sudo ip netns exec ue1 ping -c 5 10.45.0.1` returns `0% packet loss`.

---

## 12) Notes About Privileges

- `sudo` is required for:
  - `ip netns` operations
  - srsUE GW setup
  - TUN/iptables setup
- Non-root runs may look partially healthy but fail at netns/GW stage.
