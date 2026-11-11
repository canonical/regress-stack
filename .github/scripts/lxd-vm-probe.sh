#!/bin/bash
# Launch a VM the way spread does and report whether it gets an IPv4
# address within spread's limit. Then keep waiting to measure how long the
# address really takes, and print how the guest spent its boot time.
# Usage: lxd-vm-probe.sh IMAGE PLAN STORAGE
# PROBE_TIMEOUT is spread's limit (180 s); PROBE_MAX_WAIT is how long to
# keep waiting after that (600 s in total).
set -uo pipefail

image=${1:-ubuntu-daily:noble}
plan=${2:-c14-m56}
storage=${3:-50GiB}
name=probe-vm
timeout=${PROBE_TIMEOUT:-180}
max_wait=${PROBE_MAX_WAIT:-600}

section() { echo; echo "===== $* ====="; }

address() {
    lxc list "$name" --format json | jq -r '
        .[0].state.network // {} | to_entries[]
        | select(.key != "lo") | .value.addresses[]
        | select(.family == "inet") | .address' | head -1
}

# Wait until the guest has an IPv4 address or $1 seconds after launch.
wait_address() {
    local addr
    while (( SECONDS - launched < $1 )); do
        addr=$(address)
        if [ -n "$addr" ]; then
            echo "address $addr after $((SECONDS - launched))s"
            return 0
        fi
        sleep 1
    done
    echo "no IPv4 address after $((SECONDS - launched))s"
    return 1
}

guest() { timeout 60 lxc exec "$name" -- sh -c "$1"; }

host_state() {
    section "host"
    lscpu | grep -i -E 'model name|^cpu\(s\)|virtuali|hypervisor'
    grep -H . /sys/module/kvm_*/parameters/nested
    uptime; free -h
}

guest_now() {
    section "guest network and cloud-init now"
    guest 'ip -4 addr; cloud-init status --long' \
        || echo "lxc exec failed: lxd-agent not reachable"
    section "guest journal so far"
    guest 'journalctl -b -o short-monotonic --no-pager | tail -150' \
        || echo "no journal"
    section "lxd network"
    lxc info "$name" | sed -n '/Network usage/,$p'
    lxc network list-leases lxdbr0
}

guest_boot() {
    section "guest boot timing"
    guest 'systemctl is-system-running --wait; systemd-analyze;
           systemd-analyze critical-chain --no-pager;
           systemd-analyze blame --no-pager | head -25'
    section "guest kernel"
    guest 'uname -r; grep -c processor /proc/cpuinfo;
           dmesg | grep -i -E "clocksource|tsc|kvm|hypervisor|random" | head -20'
}

echo "runner: ${RUNNER_NAME:-unknown}"
echo "image: $image plan: $plan storage: $storage"
host_state
start=$SECONDS
lxc launch "$image" "$name" --vm -t "$plan" -d "root,size=$storage" || exit 1
launched=$SECONDS
echo "launched after $((launched - start))s"

status=0
if ! wait_address "$timeout"; then
    status=1
    echo "::error::no IPv4 address within ${timeout}s on ${RUNNER_NAME:-unknown}"
    guest_now
    if wait_address "$max_wait"; then
        echo "::warning::address came after $((SECONDS - launched))s, past spread's ${timeout}s"
    fi
fi
guest_boot
lxc delete -f "$name"
exit "$status"
