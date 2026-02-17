#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="/home/abdul-moiz-soomro/prj/group_studies"

cmd="${1:-}"

if [[ -z "$cmd" ]]; then
  echo "Usage: $0 <step>"
  echo "Steps:"
  echo "  open5gs-prereqs"
  echo "  open5gs-build"
  echo "  open5gs-run"
  echo "  open5gs-iptables"
  echo "  open5gs-webui"
  echo "  ric-up"
  echo "  gnb-zmq-run"
  echo "  srsran-project-build-fallback"
  echo "  srsran-4g-build-ubuntu24-fallback"
  echo "  srsue-run"
  echo "  ue-ping"
  echo "  usrp-drivers"
  echo "  usrp-missing-libs"
  echo "  gnb-usrp-run"
  echo "  kpimon-rebuild"
  exit 1
fi

case "$cmd" in
  open5gs-prereqs)
    sudo ip tuntap add name ogstun mode tun || true
    sudo ip addr add 10.45.0.1/16 dev ogstun || true
    sudo ip link set ogstun up
    ;;

  open5gs-build)
    cd "$ROOT_DIR/open5gs"
    git pull
    meson build --prefix="$(pwd)"/install
    ninja -C build
    ;;

  open5gs-run)
    cd "$ROOT_DIR/open5gs"
    ./build/tests/app/5gc
    ;;

  open5gs-iptables)
    sudo iptables -I FORWARD -i ogstun -j ACCEPT
    sudo iptables -I FORWARD -o ogstun -j ACCEPT
    ;;

  open5gs-webui)
    cd "$ROOT_DIR/open5gs/webui"
    npm ci
    npm run build
    ;;

  ric-up)
    cd "$ROOT_DIR/oran-sc-ric"
    docker compose up
    ;;

  gnb-zmq-run)
    cd "$ROOT_DIR/srsRAN_Project/build/apps/gnb"
    sudo ./gnb -c gnb_zmq.yaml e2 --addr="10.0.2.10" --bind_addr="10.0.2.1"
    ;;

  srsran-project-build-fallback)
    cd "$ROOT_DIR"
    git clone https://github.com/srsran/srsRAN_Project.git
    cd srsRAN_Project
    mkdir -p build
    cd build
    cmake ../ -DENABLE_EXPORT=ON -DENABLE_ZEROMQ=ON -DAUTO_DETECT_ISA=OFF
    make -j"$(nproc)"
    ;;

  srsran-4g-build-ubuntu24-fallback)
    sudo apt install -y gcc-11 g++-11
    cd "$ROOT_DIR"
    git clone https://github.com/srsRAN/srsRAN_4G.git
    cd srsRAN_4G
    mkdir -p build && cd build
    export CC="$(which gcc-11)"
    export CXX="$(which g++-11)"
    cmake ../ -B build
    cmake --build build -j"$(nproc)"
    ;;

  srsue-run)
    cd "$ROOT_DIR/srsRAN_4G/build/srsue/src"
    sudo ip netns add ue1 || true
    sudo ./srsue ue_zmq.conf
    ;;

  ue-ping)
    sudo ip netns exec ue1 ping -i 0.1 10.45.0.1
    ;;

  usrp-drivers)
    sudo apt-get install -y libuhd-dev uhd-host
    sudo uhd_images_downloader
    uhd_find_devices
    ;;

  usrp-missing-libs)
    sudo apt-get install -y libpcsclite-dev libbladerf-dev soapysdr-module-all libsctp-dev libuhd-dev doxygen libdwarf-dev libelf-dev binutils-dev libdw-dev libmbedtls-dev libyaml-cpp-dev lksctp-tools libsctp-dev libconfig++-dev
    ;;

  gnb-usrp-run)
    cd "$ROOT_DIR/srsRAN_Project/build/apps/gnb/"
    sudo ./gnb -c gnb_rf_b200_tdd_n78_20mhz.yml e2 --addr="10.0.2.10" --bind_addr="10.0.2.1"
    ;;

  kpimon-rebuild)
    cd "$ROOT_DIR/oran-sc-ric"
    docker compose build --no-cache python_xapp_runner
    docker compose up -d python_xapp_runner
    docker compose exec python_xapp_runner ./kpm_mon_xapp.py
    ;;

  *)
    echo "Unknown step: $cmd"
    exit 1
    ;;
esac
