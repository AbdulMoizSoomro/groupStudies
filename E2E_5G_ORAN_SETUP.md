End-to-End 5G O-RAN Network Setup Guide

Author: M.Sc Manasik Hassan  
Date: 2025-07-24  
Version: 3.0.0

This workspace is prepared to follow the guide in exact component order:
1. Open5GS Core
2. O-RAN OSC Near RT RIC
3. gNB (Option 1 or Option 2)
4. COTS UE (for Option 2)
5. KPIMON xApp

## 1) Open5GS Core

Reference: https://open5gs.org/open5gs/docs/guide/02-building-open5gs-from-sources/

From workspace root (`/home/abdul-moiz-soomro/prj/group_studies`):

```bash
cd open5gs

# 1. Install MongoDB
# Follow official Open5GS build guide for distro-specific MongoDB steps.

# 2. Configure TUN device
sudo ip tuntap add name ogstun mode tun
sudo ip addr add 10.45.0.1/16 dev ogstun
sudo ip link set ogstun up

# 3. Build Open5GS from source (latest)
git pull
meson build --prefix=`pwd`/install
ninja -C build

# 4. Configuration
# Replace open5gs/build/configs/sample.yaml with the guideline-provided sample.yaml
# Then run core:
./build/tests/app/5gc

# 5. Route rules for UE WAN + DL traffic
sudo iptables -I FORWARD -i ogstun -j ACCEPT
sudo iptables -I FORWARD -o ogstun -j ACCEPT

# 6. Build WebUI
cd webui
npm ci
npm run build
```

Register subscriber in WebUI:
- IMSI: `001010000000101`
- Ki: `0C0A34601D4F07677303652C0462535B`
- Opc: `63BFA50EE6523365FF14C1F45F88737B`
- APN: `internet`

## 2) O-RAN OSC Near RT RIC

```bash
cd /home/abdul-moiz-soomro/prj/group_studies
cd oran-sc-ric

# Replace this file with the guideline-provided version before start:
# ./docker-compose.yml

docker compose up
```

## 3) gNB

### Option 1: srsgNB + srsUE (No Hardware)

Reference: https://docs.srsran.com/projects/project/en/latest/tutorials/source/near-rt-ric/source/index.html

```bash
cd /home/abdul-moiz-soomro/prj/group_studies/srsRAN_Project/build/apps/gnb
sudo ./gnb -c gnb_zmq.yaml e2 --addr="10.0.2.10" --bind_addr="10.0.2.1"
```

If build issues occur with `srsRAN_Project`:

```bash
cd /home/abdul-moiz-soomro/prj/group_studies
git clone https://github.com/srsran/srsRAN_Project.git
cd srsRAN_Project
mkdir build
cd build
cmake ../ -DENABLE_EXPORT=ON -DENABLE_ZEROMQ=ON -DAUTO_DETECT_ISA=OFF
make -j`nproc`
```

If build issues occur with `srsRAN_4G` on Ubuntu 24.04:

```bash
sudo apt install gcc-11 g++-11
cd /home/abdul-moiz-soomro/prj/group_studies
git clone https://github.com/srsRAN/srsRAN_4G.git
cd srsRAN_4G
mkdir build && cd build
export CC=$(which gcc-11)
export CXX=$(which g++-11)
cmake ../ -B build
cmake --build build -j$(nproc)
```

Configure and run `srsUE`:

```bash
# Guideline-provided ue_zmq.conf is expected here:
# /home/abdul-moiz-soomro/prj/group_studies/srsRAN_4G/build/build/srsue/src/ue_zmq.conf

cd /home/abdul-moiz-soomro/prj/group_studies/srsRAN_4G/build/build/srsue/src
sudo ip netns add ue1
sudo ./srsue ue_zmq.conf

# UE to core ping
sudo ip netns exec ue1 ping -i 0.1 10.45.0.1
```

### Option 2: USRPs + COTS UEs

```bash
# 1) USRP drivers
sudo apt-get install libuhd-dev uhd-host
sudo uhd_images_downloader
uhd_find_devices

# 2) Missing libraries
sudo apt-get install libpcsclite-dev libbladerf-dev soapysdr-module-all libsctp-dev libuhd-dev doxygen libdwarf-dev libelf-dev binutils-dev libdw-dev libmbedtls-dev libyaml-cpp-dev lksctp-tools libsctp-dev libconfig++-dev

# 3) gNB run
cd /home/abdul-moiz-soomro/prj/group_studies/srsRAN_Project/build/apps/gnb/
sudo ./gnb -c gnb_rf_b200_tdd_n78_20mhz.yml e2 --addr="10.0.2.10" --bind_addr="10.0.2.1"
```

## 4) COTS UE

- Insert SIM with PLMN ID `00101` into a 5G phone supporting private network PLMN IDs.
- Ensure APN `internet` is selected and enabled.
- Turn on device and verify UL/DL traffic.

## 5) KPIMON xApp

Reference: https://docs.srsran.com/projects/project/en/latest/tutorials/source/near-rt-ric/source/index.html

If xApp Dockerfile/libraries are changed:

```bash
cd /home/abdul-moiz-soomro/prj/group_studies/oran-sc-ric
docker compose build --no-cache python_xapp_runner
docker compose up -d python_xapp_runner
docker compose exec python_xapp_runner ./kpm_mon_xapp.py
```

---

Workspace status already aligned with guide file-placement:
- `gnb_zmq.yaml` copied to `srsRAN_Project/build/apps/gnb/gnb_zmq.yaml`
- `ue_zmq.conf` copied to `srsRAN_4G/build/build/srsue/src/ue_zmq.conf`

Pending manual replacement files from guideline package (if different from current files):
- `open5gs/build/configs/sample.yaml`
- `oran-sc-ric/docker-compose.yml`
