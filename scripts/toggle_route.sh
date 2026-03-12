#!/bin/bash

# Configuration
NS="ue1"
GTP_GW="10.45.0.1"
GTP_DEV="tun_srsue"
OWRT_GW="192.168.88.1"
OWRT_DEV="ue1owrt"

# Get current default route device
CURRENT_DEV=$(sudo ip netns exec "$NS" ip route show default | awk '{print $5}')

if [[ "$CURRENT_DEV" == "$GTP_DEV" ]]; then
    echo "Current route: Open5GS ($GTP_DEV). Switching to OpenWrt ($OWRT_DEV)..."
    sudo ip netns exec "$NS" ip route del default
    sudo ip netns exec "$NS" ip route add default via "$OWRT_GW" dev "$OWRT_DEV"
elif [[ "$CURRENT_DEV" == "$OWRT_DEV" ]]; then
    echo "Current route: OpenWrt ($OWRT_DEV). Switching to Open5GS ($GTP_DEV)..."
    sudo ip netns exec "$NS" ip route del default
    sudo ip netns exec "$NS" ip route add default via "$GTP_GW" dev "$GTP_DEV"
else
    echo "Unknown default route or no default route found. Resetting to Open5GS..."
    sudo ip netns exec "$NS" ip route del default 2>/dev/null
    sudo ip netns exec "$NS" ip route add default via "$GTP_GW" dev "$GTP_DEV"
fi

# Show final state
echo "New routing table:"
sudo ip netns exec "$NS" ip route | grep default
