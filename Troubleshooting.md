## Common Problems and Fixes

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

### M) OpenWrt Destination Port Unreacable

The `Destination Port Unreachable` error can be caused by a conflict in the OpenWrt firewall rules. The container coud have active `fw4` rules (the default OpenWrt firewall) alongside the manual `nftables` rules applied in the guide. The `fw4` rules can rejecting the forwarded traffic by default.

we can resolve this by flushing the existing `nftables` rules and reapplying a minimal, functional configuration.

### Steps taken to fix:
1.  **Stopped conflicting firewall rules**: Flushed the default `fw4` chains that were rejecting traffic.
2.  **Re-applied NAT and Forwarding**:
    -   Set `inet filter forward` policy to `accept`.
    -   Ensured `masquerade` is enabled on the `eth0` (WAN) interface.

```bash
docker exec openwrt_router sh -c "
  nft flush ruleset
  nft add table inet filter
  nft 'add chain inet filter input { type filter hook input priority 0; policy accept; }'
  nft 'add chain inet filter forward { type filter hook forward priority 0; policy accept; }'
  nft add table ip nat
  nft 'add chain ip nat postrouting { type nat hook postrouting priority 100; policy accept; }'
  nft add rule ip nat postrouting oifname 'eth0' counter masquerade
"
```
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


