# Troubleshooting

### "No metrics endpoints found in config"

**Problem**: Config file is valid YAML but has no `metrics.server` sections.

**Solution**:
1. Verify `metrics` section in each NF config:
   ```yaml
   amf:
     metrics:
       server:
         - address: 127.0.0.2
           port: 9090
   ```
2. Debug with: `python app.py --debug`

### "Config file not found"

**Problem**: Default hardcoded path doesn't match your setup.

**Solution**:
```bash
export OPEN5GS_CONFIG=/path/to/your/sample.yaml
python app.py
```

Or use explicit flag:
```bash
python app.py --config /path/to/sample.yaml
```

### "Connection failed for amf: HTTPConnectionPool..."

**Problem**: Prometheus endpoint unreachable.

**Causes**:
- Open5GS NF not running
- Metrics disabled in config
- Firewall blocking localhost

**Solution**:
```bash
# Check if service is running and listening
netstat -tlnp | grep 9090

# Verify manual curl works
curl http://127.0.0.2:9090/metrics

# Run with debug to see connection details
python app.py --debug
```

### "Timeout fetching metrics from ..."

**Problem**: NF is running but slow or unresponsive.

**Solution**: Increase timeout:
```bash
python app.py --timeout 5.0
```

### "OpenWrt container metrics are empty"

**Problem**: The specified OpenWrt container is not running or inaccessible.

**Solution**:
- Verify Docker container name (default is `openwrt_router`):
  ```bash
  docker ps --format 'table {{.Names}}\t{{.Status}}'
  ```
- Set the container explicitly:
  ```bash
  python app.py --openwrt-container openwrt_router --json
  ```
- Confirm `/proc` in the OpenWrt container:
  ```bash
  docker exec openwrt_router cat /proc/net/dev
  ```
