
We are using `Ubuntu-22.04`

# 1. Open5gs setup
Go to [open5gs](https://open5gs.org/open5gs/docs/guide/02-building-open5gs-from-sources/ )
## Setup Following from the [open5gs][https://open5gs.org/open5gs/docs/guide/02-building-open5gs-from-sources/ ]
There should be certain configurations that needs to be setup for the open5gs and srsRAN_Project, that need to match from the both side, open5gs and srsRAN_Project(gnb). 

Setup Following from the documentation:
1. Getting MongoDB
2. Setting up TUN device (not persistent after rebooting)
	- IP : `10.45.0.1/16`
3. Building Open5GS
	1. When running tests ensure the mongodb is running
	```bash
		sudo systemctl status mongod # check status
		sudo systemctl restart mongod # Turn on the service
	```
	2. Expected output of the tests
		```bash
			Ok:                 15  
			Expected Fail:      0   
			Fail:               0   
			Unexpected Pass:    0   
			Skipped:            0   
			Timeout:            0   
		```

	3. change the configs parameters in the files:
		- `open5gs/build/configs/sample.yaml`
		- `open5gs/configs/sample.yaml.in`
		- `open5gs/configs/open5gs/amf.yaml.in`
		- `open5gs/configs/open5gs/nrf.yaml.in`
		```bash
	-          mcc: 999
	-          mnc: 70
	-		   tac: 1
	+          mcc: 001
	+          mnc: 01
	+		   tac: 7
		```
		4. Run the open5gs with
		```bash
				./build/tests/app/5gc
		```

		5. Ensure that the UE's information is in the mongodb.
		```bash
		# 1. Path to the database control script (assuming your project root is ~/prj)
# Change '~/prj' to your actual full path if it is different.
DBCTL="open5gs/build/misc/db/open5gs-dbctl"


# 2. Add the UE using explicit Profile B credentials (IMSI, KI, OPC, APN)
bash "open5gs/build/misc/db/open5gs-dbctl" add_ue_with_apn \
  001010123456780 \
  00112233445566778899aabbccddeeff \
  63BFA50EE6523365FF14C1F45F88737D \
  srsapn

# 3. Show the subscriber list to verify the entry exists
bash "open5gs/build/misc/db/open5gs-dbctl" showfiltered
		```
		
---

 ***Ensure Zeromq is installed*** it is vital for UE(srsRAN_4G) and gNb(srsRAN_Project)
 ```bash
 sudo apt-get install libzmq3-dev
 ```

---
# 2. Setup UE srsran 4G
1. Install the relevent packages
```bash
   sudo apt-get install build-essential cmake libfftw3-dev libmbedtls-dev libboost-program-options-dev libconfig++-dev libsctp-dev
```      
2. Go to [srsRan 4G with zmq virtual radios](https://docs.srsran.com/projects/4g/en/latest/app_notes/source/zeromq/source/index.html)
3. Follow [ZeroMQ Installation](https://docs.srsran.com/projects/4g/en/latest/app_notes/source/zeromq/source/index.html#zeromq-installation "Link to this heading") guide for the build.
```bash
git clone https://github.com/srsRAN/srsRAN_4G.git
cd srsRAN_4G
mkdir build
cd build
cmake ../
make
```

---
# 3. Setup srsRAN_Project

1. Go to [srsran Project Github](https://github.com/srsran/srsran_project)
2. install required packages for
```bash
sudo apt-get install cmake make gcc g++ pkg-config libfftw3-dev libmbedtls-dev libsctp-dev libyaml-cpp-dev
```
3. Run the following to fetch and build the srsRAN_Project
```bash
git clone https://github.com/srsran/srsRAN_Project.git
cd srsRAN_Project
mkdir build
cd build
cmake ../ -DENABLE_EXPORT=ON -DENABLE_ZEROMQ=ON
make -j`nproc`
```

---
# 4. Setup  ORAN SC RIC
1. We require [docker](https://docs.docker.com/engine/install/ubuntu/), and [docker compose](https://docs.docker.com/compose/install/#plugin-linux-only), you can follow the [official Docker] for the setup
2. Using Docker container, we can setup the oran sc ric
```bash
git clone https://github.com/AbdulMoizSoomro/group-studies-oran-sc-ric
cd ./oran-sc-ric
docker compose up
```

Ensure that you are running the kpm_mon_xapp.py script in the container, which collects KPIs from the gNB and exposes them to Prometheus. You can check the logs of the kpm_mon_xapp.py container to verify that it is running correctly and collecting data.

```bash
docker compose exec -d python_xapp_runner python3 /opt/xApps/kpm_mon_xapp.py
```

The default kpm_mon_xapp.py by `https://github.com/srsran/oran-sc-ric` has been modified to use the O-RAN RIC E2 interface.
KPI data is pushed to Prometheus.
The app.py service fetches these metrics from Prometheus using the configured IP address.

---



# Run the Services
## 1. Open5gs
```bash
sudo ./open5gs/build/tests/app/5gc # Starts all the open5gs servicese
```
## 2. Oran sc ric
```bash
docker compose up -d # run oran sc ric containers
docker compose exec -d python_xapp_runner python3 /opt/xApps/kpm_mon_xapp.py

```
## 3. gNB
```bash

# Discover current E2TERM IP on docker bridge (changes across restarts/interface updates)
E2_IP=$(docker inspect -f '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}' ric_e2term)
echo "Using E2_IP=$E2_IP"

# Runs with gnb_zmq.yaml file. 
# !!! Ensure gnb configs match open5gs !!!
# Note: --bind_addr must be the host's IP on the docker bridge (usually 10.0.2.1)
sudo "./srsRAN_Project/build/apps/gnb/gnb" \
  -c "./groupStudies/gnb_zmq.yaml" \
  log --all_level=info --e2ap_level=debug --ngap_level=info \
  e2 --enable_du_e2=true --enable_cu_cp_e2=true --enable_cu_up_e2=true \
     --e2sm_kpm_enabled=true --e2sm_rc_enabled=true \
     --addr="$E2_IP" --port=36421 --bind_addr="10.0.2.1"
```

Expected output
```bash
--== srsRAN gNB (commit 4bf1543936) ==--

Lower PHY in executor sequential baseband mode.
Available radio types: zmq.
Cell pci=1, bw=10 MHz, 1T1R, dl_arfcn=368500 (n3), dl_freq=1842.5 MHz, dl_ssb_arfcn=368410, ul_freq=1747.5 MHz

N2: Connection to AMF on 127.0.0.5:38412 completed
E2AP: Connection to Near-RT-RIC on 10.0.2.10:36421 completed
==== gNB started ===
Type <h> to view help
```
## 4. UE

```bash
sudo ip netns del "ue1" 2>/dev/null || true
sudo ip netns add "ue1"

sudo "./srsRAN_4G/build/srsue/src/srsue" \
  "./groupStudies/ue_zmq.conf"
```

Expected output
```bash
Active RF plugins: libsrsran_rf_zmq.so
Inactive RF plugins: 
Reading configuration file ./groupStudies/ue_zmq.conf...

Built in Release mode using commit 6bcbd9e5bf on branch master.

Opening 1 channels in RF device=zmq with args=tx_port=tcp://127.0.0.1:2001,rx_port=tcp://127.0.0.1:2000,base_srate=11.52e6
Supported RF device list: zmq file
CHx base_srate=11.52e6
Current sample rate is 1.92 MHz with a base rate of 11.52 MHz (x6 decimation)
CH0 rx_port=tcp://127.0.0.1:2000
CH0 tx_port=tcp://127.0.0.1:2001
Current sample rate is 11.52 MHz with a base rate of 11.52 MHz (x1 decimation)
Current sample rate is 11.52 MHz with a base rate of 11.52 MHz (x1 decimation)
Waiting PHY to initialize ... done!
Attaching UE...
Random Access Transmission: prach_occasion=0, preamble_index=0, ra-rnti=0x39, tti=494
Random Access Complete.     c-rnti=0x4601, ta=0
RRC Connected
PDU Session Establishment successful. IP: 10.45.0.2
RRC NR reconfiguration successful.
```

---

# Open5GS + UE1 Routing Guide
## Ping ue1 > Open5GS (10.45.0.1)

Note:
When you restart your Open5GS core or your machine, the ogstun interface may be recreated or reset. You can use this command to quickly restore it if pings fail again:

```bash
sudo ip link set ogstun up
sudo ip addr add 10.45.0.1/16 dev ogstun
```

```bash
sudo ip netns exec ue1 ip -brief addr          # List all IP addresses assigned to interfaces inside the 'ue1' namespace in a concise format.
sudo ip netns exec ue1 ip route               # Show the routing table for the 'ue1' namespace to see where traffic is being directed.
sudo ip netns exec "ue1" ping -c 5 "10.45.0.1" # Send 5 ICMP echo requests (pings) from the 'ue1' namespace to the IP 10.45.0.1 to test connectivity.
```

#### ping output
```bash
sudo ip netns exec "ue1" ping -c 5 "10.45.0.1"
PING 10.45.0.1 (10.45.0.1) 56(84) bytes of data.
64 bytes from 10.45.0.1: icmp_seq=1 ttl=64 time=34.6 ms
64 bytes from 10.45.0.1: icmp_seq=2 ttl=64 time=20.5 ms
64 bytes from 10.45.0.1: icmp_seq=3 ttl=64 time=27.3 ms
64 bytes from 10.45.0.1: icmp_seq=4 ttl=64 time=31.5 ms
64 bytes from 10.45.0.1: icmp_seq=5 ttl=64 time=36.2 ms

--- 10.45.0.1 ping statistics ---
5 packets transmitted, 5 received, 0% packet loss, time 3996ms
rtt min/avg/max/mdev = 20.541/30.021/36.228/5.625 ms
```

#### tcpdump in ogstun

```bash
sudo tcpdump -i ogstun icmp
tcpdump: verbose output suppressed, use -v[v]... for full protocol decode
listening on ogstun, link-type RAW (Raw IP), snapshot length 262144 bytes
02:53:14.943471 IP 10.45.0.2 > igmp-query.fem.tu-ilmenau.de: ICMP echo request, id 40478, seq 1, length 64
02:53:14.943500 IP igmp-query.fem.tu-ilmenau.de > 10.45.0.2: ICMP echo reply, id 40478, seq 1, length 64
02:53:15.984057 IP 10.45.0.2 > igmp-query.fem.tu-ilmenau.de: ICMP echo request, id 40478, seq 2, length 64
02:53:15.984074 IP igmp-query.fem.tu-ilmenau.de > 10.45.0.2: ICMP echo reply, id 40478, seq 2, length 64
02:53:17.029354 IP 10.45.0.2 > igmp-query.fem.tu-ilmenau.de: ICMP echo request, id 40478, seq 3, length 64
02:53:17.029371 IP igmp-query.fem.tu-ilmenau.de > 10.45.0.2: ICMP echo reply, id 40478, seq 3, length 64
02:53:18.048521 IP 10.45.0.2 > igmp-query.fem.tu-ilmenau.de: ICMP echo request, id 40478, seq 4, length 64
02:53:18.048548 IP igmp-query.fem.tu-ilmenau.de > 10.45.0.2: ICMP echo reply, id 40478, seq 4, length 64
02:53:19.089570 IP 10.45.0.2 > igmp-query.fem.tu-ilmenau.de: ICMP echo request, id 40478, seq 5, length 64
02:53:19.089595 IP igmp-query.fem.tu-ilmenau.de > 10.45.0.2: ICMP echo reply, id 40478, seq 5, length 64
^C
10 packets captured
10 packets received by filter
0 packets dropped by kernel
```


## Ping ue1 > Open5GS > Internet


```bash
# 1. Add default route inside the UE namespace
sudo ip netns exec ue1 ip route add default via 10.45.0.1 dev tun_srsue 2>/dev/null || true

# 2. Enable IP forwarding on the host
sudo sysctl -w net.ipv4.ip_forward=1

# 3. Setup NAT (Masquerade) on the host egress interface (assuming eth0)
sudo iptables -t nat -C POSTROUTING -o eth0 -j MASQUERADE 2>/dev/null || \
  sudo iptables -t nat -A POSTROUTING -o eth0 -j MASQUERADE

# 4. Allow forwarding from the TUN interface to the Internet
sudo iptables -C FORWARD -i ogstun -o eth0 -j ACCEPT 2>/dev/null || \
  sudo iptables -I FORWARD 1 -i ogstun -o eth0 -j ACCEPT

# 5. Allow return traffic (Established/Related) back to the TUN
sudo iptables -C FORWARD -i eth0 -o ogstun -m conntrack --ctstate RELATED,ESTABLISHED -j ACCEPT 2>/dev/null || \
  sudo iptables -I FORWARD 1 -i eth0 -o ogstun -m conntrack --ctstate RELATED,ESTABLISHED -j ACCEPT

# 6. Connectivity Tests
sudo ip netns exec ue1 traceroute -n -m 5 8.8.8.8 || sudo ip netns exec ue1 tracepath -n 8.8.8.8
sudo ip netns exec ue1 ping -c 5 8.8.8.8
```

### Expected output
#### 1. Kernel and Firewall Status


```bash
net.ipv4.ip_forward = 1                                      # Success: The host is now acting as a router.
MASQUERADE  all opt -- in * out eth0                         # Success: Your private UE traffic is being hidden behind your host's IP.
ACCEPT  all opt -- in ogstun out eth0                        # Success: Firewall is allowing traffic to flow from the 5G tunnel to the Internet.
ACCEPT  all opt -- in eth0 out ogstun  ctstate ESTABLISHED   # Success: Firewall is allowing the Internet's response back to the UE.
```

---

#### 2. The Tracepath (Network Hops)


```bash
 1:  10.45.0.1      43.229ms    # The UE has hit the 'ogstun' interface (the Gateway of your Core Network).
 2:  172.24.16.1    42.845ms    # This is the first hop outside your local machine (your local router or ISP gateway).
 3-7: ...                       # Various ISP and backbone routers carrying your packet toward Google.
 8:  no reply                   # Normal: Many core routers ignore tracepath/ICMP packets for security.
```

---

#### 3. The Final Ping (8.8.8.8)


```bash
64 bytes from 8.8.8.8: icmp_seq=1 ttl=115 time=32.6 ms       # Success: A packet made it from the UE namespace to Google and back!
...
5 packets transmitted, 5 received, 0% packet loss            # Perfect: No drops. Your NAT and Routing configuration is 100% correct.
```

---

# OpenWrt + UE1 Namespace Routing Setup Guide

**Prerequisites:** * Docker installed and running.

- `iproute2` installed on the host.
    
- `ue1` network namespace already created (`sudo ip netns add ue1`).
    

## 1. Create Dedicated Docker Networks

Create isolated WAN and LAN bridge networks for the OpenWrt container.

```bash
# Clean up existing networks if they exist (optional/for fresh starts)
docker network rm owrt_wan owrt_lan 2>/dev/null || true

# Create the WAN network (simulates the external internet connection)
docker network create --driver bridge --subnet 172.31.0.0/24 --gateway 172.31.0.1 owrt_wan

# Create the LAN network (simulates the internal local network for UE devices)
docker network create --driver bridge --subnet 192.168.88.0/24 --gateway 192.168.88.254 owrt_lan
```

## 2. Deploy the OpenWrt Container

Start the container in privileged mode, attach it to the WAN network upon creation, and subsequently attach the LAN network.


```bash
# Remove any existing container with the same name
docker rm -f openwrt_router 2>/dev/null || true

# Start OpenWrt attached to the WAN network
docker run -d \
  --name openwrt_router \
  --hostname openwrt-router \
  --privileged \
  --network owrt_wan \
  --ip 172.31.0.2 \
  openwrt/rootfs:latest /sbin/init

# Attach the LAN network to the running container
docker network connect --ip 192.168.88.1 owrt_lan openwrt_router
```

## 3. Configure OpenWrt Routing and NAT

Because standard `uci` reloads can timeout in this specific Docker context, configure the interfaces, routing, and `nftables` firewall directly via runtime commands.


```bash
docker exec openwrt_router sh -lc '
# 1. Tear down the default OpenWrt bridge (br-lan) to avoid conflicts
ip link set br-lan down || true
ip addr flush dev br-lan || true
ip link set eth0 nomaster || true
ip link set eth1 nomaster || true

# 2. Bring up raw interfaces and flush old IP assignments
ip link set eth0 up
ip link set eth1 up
ip addr flush dev eth0 || true
ip addr flush dev eth1 || true

# 3. Assign static IPs to the interfaces
ip addr add 172.31.0.2/24 dev eth0      # WAN Interface
ip addr add 192.168.88.1/24 dev eth1    # LAN Interface

# 4. Set the default route to exit via the Docker WAN bridge gateway
ip route replace default via 172.31.0.1 dev eth0

# 5. Enable IP forwarding in the kernel
echo 1 > /proc/sys/net/ipv4/ip_forward

# 6. Configure nftables for NAT and forwarding
nft flush ruleset
nft add table inet filter

# Allow all input traffic
nft "add chain inet filter input { type filter hook input priority 0; policy accept; }"

# Drop forwarding by default, explicitly allow LAN -> WAN and established return traffic
nft "add chain inet filter forward { type filter hook forward priority 0; policy drop; }"
nft add rule inet filter forward iifname "eth1" oifname "eth0" counter accept
nft add rule inet filter forward iifname "eth0" oifname "eth1" ct state related,established counter accept

# Setup Masquerade (NAT) for outbound WAN traffic
nft add table ip nat
nft "add chain ip nat postrouting { type nat hook postrouting priority 100; policy accept; }"
nft add rule ip nat postrouting oifname "eth0" counter masquerade
'
```

## 4. Connect the UE1 Namespace to the LAN

Create a Virtual Ethernet (`veth`) pair. Move one end into the `ue1` namespace and attach the other end to the Docker LAN bridge on the host.


```bash
# Clean up existing veth interfaces if they exist
sudo -n ip link del ue1owrth 2>/dev/null || true
sudo -n ip netns exec ue1 ip link del ue1owrt 2>/dev/null || true

# Dynamically extract the host bridge interface name for owrt_lan
# Docker creates bridges formatted as br-<first_12_chars_of_network_id>
LAN_BR="br-$(docker network inspect owrt_lan -f '{{.Id}}' | cut -c 1-12)"

# Create the veth pair
sudo -n ip link add ue1owrt type veth peer name ue1owrth

# Move the UE end into the ue1 namespace and configure it
sudo -n ip link set ue1owrt netns ue1
sudo -n ip netns exec ue1 ip addr add 192.168.88.2/24 dev ue1owrt
sudo -n ip netns exec ue1 ip link set ue1owrt up

# Attach the host end to the OpenWrt LAN bridge and bring it up
sudo -n ip link set ue1owrth master $LAN_BR
sudo -n ip link set ue1owrth up

# Route all traffic in the ue1 namespace through the OpenWrt LAN IP
sudo -n ip netns exec ue1 ip route replace default via 192.168.88.1 dev ue1owrt
```

## 5. Verification Commands

Run these to ensure traffic is flowing correctly from the namespace, through the container, and out to the internet.


```bash
# 1. Test ping from UE1 to Google DNS
sudo -n ip netns exec ue1 ping -c 4 8.8.8.8

# 2. Check OpenWrt firewall counters to verify traffic actually passed through NAT
docker exec openwrt_router sh -lc 'nft list chain inet filter forward; nft list chain ip nat postrouting'
```


Expected output: ICMP tcpdump while UE traffic goes through OpenWrt

```bash

$ sudo -n tcpdump -ni any "icmp and (net 192.168.88.0/24 or host 172.31.0.2)"
tcpdump: data link type LINUX_SLL2
tcpdump: verbose output suppressed, use -v[v]... for full protocol decode
listening on any, link-type LINUX_SLL2 (Linux cooked v2), snapshot length 262144 bytes

05:48:27.262740 ue1owrth P   IP 192.168.88.2 > 8.8.8.8: ICMP echo request, id 5885, seq 4484, length 64
05:48:27.262774 veth8acffc8 P   IP 172.31.0.2 > 8.8.8.8: ICMP echo request, id 5885, seq 4484, length 64
05:48:27.270936 veth8acffc8 Out IP 8.8.8.8 > 172.31.0.2: ICMP echo reply, id 5885, seq 4484, length 64
05:48:27.270958 ue1owrth Out IP 8.8.8.8 > 192.168.88.2: ICMP echo reply, id 5885, seq 4484, length 64
...


112 packets captured
142 packets received by filter
0 packets dropped by kernel
```


Expected output: UE tracepath via OpenWrt

```text

$ sudo ip netns exec ue1 traceroute -n -m 5 8.8.8.8 || sudo ip netns exec ue1 tracepath -n 8.8.8.8
exec of "traceroute" failed: No such file or directory
 1?: [LOCALHOST]                      pmtu 1500
 1:  192.168.88.1                                          0.134ms
 2:  172.31.0.1                                            0.078ms
 3:  172.24.16.1                                           0.307ms
 4:  141.24.54.1                                           1.048ms
 5:  10.15.0.2                                             0.818ms
 6:  141.24.249.97                                         0.994ms
 7:  188.1.238.1                                           3.118ms
 8:  188.1.145.134                                         6.844ms
 9:  no reply
```


What confirms success:
- You see LAN-side source 192.168.88.2 and NAT-side source 172.31.0.2 for the same ICMP sequence values.
- Replies are visible on both sides, proving forward and return path through OpenWrt.
- First tracepath hop is 192.168.88.1, confirming UE default gateway is OpenWrt.
# openwrt_open5gs_kpi_app

Read README.md file in [openwrt_open5gs_kpi_app](./openwrt_open5gs_kpi_app/README.md) for the setup and usage of the KPI app.