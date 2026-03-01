# Open5GS KPI App

Simple Python app to fetch KPI metrics from Open5GS NF Prometheus endpoints.

## What it does

- Reads Open5GS config (default: `open5gs/build/configs/sample.yaml`)
- Auto-discovers `metrics.server` endpoints for NFs (AMF/SMF/UPF/MME, etc.)
- Scrapes `/metrics` and summarizes common KPIs
- Probes OpenWrt host (ICMP + HTTP) and includes status in output
- Supports one-shot mode and watch mode

## Setup

```bash
cd /home/abdul-moiz-soomro/prj/group_studies/open5gs_kpi_app
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Run

One-shot human-readable:

```bash
python app.py
```

JSON output:

```bash
python app.py --json
```

Watch every 5 seconds:

```bash
python app.py --watch 5
```

Probe a specific OpenWrt host:

```bash
python app.py --openwrt-host 192.168.142.200
```

Disable OpenWrt probing:

```bash
python app.py --no-openwrt
```

Optional LuCI RPC auth (if enabled on your OpenWrt):

```bash
python app.py --openwrt-user root --openwrt-password '<password>'
```

Custom config:

```bash
python app.py --config /path/to/sample.yaml
```

## Notes

- Open5GS `5gc` must be running.
- Endpoints are typically loopback addresses from your `sample.yaml` (e.g. `127.0.0.5:9090`).
