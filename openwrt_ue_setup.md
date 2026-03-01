# OpenWrt + UE1 Routing Setup (Executed and Verified)

Date: 2026-03-01  
Workspace: `/home/abdul-moiz-soomro/prj/group_studies`

This document records exactly what was done to:
1. Start OpenWrt container.
2. Verify OpenWrt is reachable.
3. Verify OpenWrt can reach Google DNS (`8.8.8.8`).
4. Connect `ue1` namespace to OpenWrt.
5. Route `ue1` traffic through OpenWrt.
6. Verify `ue1 -> OpenWrt -> Internet` with counters.

No assumptions were used; all values below were discovered from this host at runtime.

---

## 1) Discovered Runtime Inputs

### OpenWrt image present

```bash
docker images --format 'table {{.Repository}}\t{{.Tag}}\t{{.ID}}\t{{.CreatedSince}}'
```

Observed relevant image:
- `openwrt/rootfs:latest` (`d3dad12e8bac`)

### Existing OpenWrt container state before setup

```bash
docker ps -a --format 'table {{.Names}}\t{{.Image}}\t{{.Status}}\t{{.Networks}}'
```

Observed:
- `openwrt_test` existed but was `Exited` and ran `sleep infinity` (not a routing setup).

---

## 2) Created Dedicated OpenWrt WAN/LAN Docker Networks

```bash
docker network inspect owrt_wan >/dev/null 2>&1 || \
  docker network create --driver bridge --subnet 172.31.0.0/24 --gateway 172.31.0.1 owrt_wan

docker network inspect owrt_lan >/dev/null 2>&1 || \
  docker network create --driver bridge --subnet 192.168.88.0/24 --gateway 192.168.88.254 owrt_lan
```

Observed network identifiers:
- `owrt_wan` id: `d5fb475bdcd192fa120978f5b501227cac1072f807e310ca7645dab1798703e8`
- `owrt_lan` id: `bc7f45ba69d436c947823e8e62b9a995a3ac82fca856ad95c43536634f022a48`

Host bridge names (derived from IDs):
- WAN bridge: `br-d5fb475bdcd1`
- LAN bridge: `br-bc7f45ba69d4`

---

## 3) Started OpenWrt Container and Attached Both Networks

```bash
docker rm -f openwrt_router >/dev/null 2>&1 || true

docker run -d \
  --name openwrt_router \
  --hostname openwrt-router \
  --privileged \
  --network owrt_wan \
  --ip 172.31.0.2 \
  openwrt/rootfs:latest /sbin/init

docker network connect --ip 192.168.88.1 owrt_lan openwrt_router
```

Validation:

```bash
docker ps --filter name=openwrt_router --format 'table {{.Names}}\t{{.Image}}\t{{.Status}}\t{{.Networks}}'
docker inspect openwrt_router --format 'OpenWrtIPs: {{range $k,$v := .NetworkSettings.Networks}}{{$k}}={{$v.IPAddress}} {{end}}'
```

Observed:
- Container `openwrt_router` is `Up`.
- IPs:
  - `owrt_wan=172.31.0.2`
  - `owrt_lan=192.168.88.1`

---

## 4) Configured OpenWrt Runtime Routing + NAT (Direct Runtime Method)

`uci` network restarts timed out in this container context, so runtime `ip`/`nft` configuration was applied directly and verified.

```bash
docker exec openwrt_router sh -lc '
ip link set br-lan down || true
ip addr flush dev br-lan || true
ip link set eth0 nomaster || true
ip link set eth1 nomaster || true

ip link set eth0 up
ip link set eth1 up
ip addr flush dev eth0 || true
ip addr flush dev eth1 || true
ip addr add 172.31.0.2/24 dev eth0
ip addr add 192.168.88.1/24 dev eth1
ip route replace default via 172.31.0.1 dev eth0

echo 1 > /proc/sys/net/ipv4/ip_forward

nft flush ruleset
nft add table inet filter
nft "add chain inet filter input { type filter hook input priority 0; policy accept; }"
nft "add chain inet filter forward { type filter hook forward priority 0; policy drop; }"
nft add rule inet filter forward iifname "eth1" oifname "eth0" counter accept
nft add rule inet filter forward iifname "eth0" oifname "eth1" ct state related,established counter accept
nft add table ip nat
nft "add chain ip nat postrouting { type nat hook postrouting priority 100; policy accept; }"
nft add rule ip nat postrouting oifname "eth0" counter masquerade
'
```

---

## 5) Verified OpenWrt Reachability and Internet Access

### Reachable from host

```bash
ping -c 3 172.31.0.2
ping -c 3 192.168.88.1
```

Observed:
- Both pings returned `0% packet loss`.

### OpenWrt can reach Google DNS

```bash
docker exec openwrt_router sh -lc 'ping -c 4 8.8.8.8'
```

Observed:
- `4 transmitted, 4 received, 0% packet loss`.

---

## 6) Connected `ue1` Namespace to OpenWrt LAN

A veth pair was created and host side attached to `owrt_lan` bridge (`br-bc7f45ba69d4`).

```bash
sudo -n ip link del ue1owrth 2>/dev/null || true
sudo -n ip netns exec ue1 ip link del ue1owrt 2>/dev/null || true

sudo -n ip link add ue1owrt type veth peer name ue1owrth
sudo -n ip link set ue1owrt netns ue1
sudo -n ip netns exec ue1 ip addr add 192.168.88.2/24 dev ue1owrt
sudo -n ip netns exec ue1 ip link set ue1owrt up
sudo -n ip link set ue1owrth master br-bc7f45ba69d4
sudo -n ip link set ue1owrth up

sudo -n ip netns exec ue1 ip route replace default via 192.168.88.1 dev ue1owrt
```

Validation:

```bash
sudo -n ip netns exec ue1 ip -brief addr
sudo -n ip netns exec ue1 ip route
sudo -n ip netns exec ue1 ping -c 3 192.168.88.1
```

Observed:
- `ue1owrt` present in `ue1` with `192.168.88.2/24`.
- Default route in `ue1` is now via `192.168.88.1` on `ue1owrt`.
- Ping to OpenWrt LAN gateway successful (`0% packet loss`).

---

## 7) Verified UE1 Traffic Goes Through OpenWrt to `8.8.8.8`

### UE1 ping test

```bash
sudo -n ip netns exec ue1 ping -c 5 8.8.8.8
```

Observed:
- `5 transmitted, 5 received, 0% packet loss`.

### OpenWrt forwarding/NAT counters (proof of path)

```bash
docker exec openwrt_router sh -lc 'nft list chain inet filter forward; nft list chain ip nat postrouting'
```

Observed counters after UE ping:
- `inet filter forward`:
  - `iifname "eth1" oifname "eth0"` counter increased (UE outbound packets).
  - `iifname "eth0" oifname "eth1" ct state established,related` counter increased (return packets).
- `ip nat postrouting`:
  - `oifname "eth0" masquerade` counter increased.

This confirms the traffic path is:
- `ue1 (192.168.88.2)` -> `OpenWrt LAN (192.168.88.1, eth1)` -> `OpenWrt WAN (172.31.0.2, eth0, NAT)` -> Internet (`8.8.8.8`).

---

## 8) Final Runtime State Snapshot

### OpenWrt container

```bash
docker ps --filter name=openwrt_router --format 'table {{.Names}}\t{{.Image}}\t{{.Status}}\t{{.Networks}}'
```

Observed:
- `openwrt_router   openwrt/rootfs:latest   Up   owrt_lan,owrt_wan`

### UE1 namespace

```bash
sudo -n ip netns exec ue1 ip -brief addr
sudo -n ip netns exec ue1 ip route
```

Observed:
- Interfaces include:
  - `tun_srsue 10.45.0.2/24`
  - `ue1owrt 192.168.88.2/24`
- Routes include:
  - `default via 192.168.88.1 dev ue1owrt`
  - `10.45.0.0/24 dev tun_srsue`
  - `192.168.88.0/24 dev ue1owrt`

---

## 9) Notes

- The OpenWrt router configuration above is runtime (inside container namespace) and will be lost if the container is recreated unless scripted at startup.
- The `ue1` veth attachment is also runtime and should be re-applied after namespace/container recreation.
