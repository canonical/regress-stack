#!/bin/bash
# Launch a VM the way spread does and report whether it gets an IPv4
# address. On failure, dump the host and guest state before deleting it.
# Usage: lxd-vm-probe.sh IMAGE PLAN STORAGE
# PROBE_TIMEOUT overrides the 180 s that spread waits for an address.
set -uo pipefail

image=${1:-ubuntu-daily:noble}
plan=${2:-c14-m56}
storage=${3:-50G}
name=probe-vm
timeout=${PROBE_TIMEOUT:-180}

section() { echo; echo "===== $* ====="; }

address() {
    lxc list "$name" --format json | jq -r '
        .[0].state.network // {} | to_entries[]
        | select(.key != "lo") | .value.addresses[]
        | select(.family == "inet") | .address' | head -1
}

wait_address() {
    local start=$SECONDS addr
    while (( SECONDS - start < timeout )); do
        addr=$(address)
        if [ -n "$addr" ]; then
            echo "address $addr after $((SECONDS - start))s"
            return 0
        fi
        sleep 1
    done
    echo "no IPv4 address after ${timeout}s"
    return 1
}

dump() {
    section "lxc list"; lxc list
    section "lxc info --show-log"; lxc info --show-log "$name"
    section "lxc console --show-log"; lxc console "$name" --show-log
    section "guest network (needs lxd-agent)"
    timeout 30 lxc exec "$name" -- sh -c \
        'ip -4 addr; networkctl status --no-pager; journalctl -b --no-pager -u systemd-networkd | tail -50; cloud-init status --long' \
        || echo "lxc exec failed: lxd-agent not reachable"
    section "lxdbr0"; lxc network show lxdbr0; lxc network info lxdbr0
    section "DHCP leases"; lxc network list-leases lxdbr0
    section "dnsmasq"; pgrep -af dnsmasq || echo "dnsmasq not running"
    section "host addresses and routes"; ip -4 addr; ip route
    section "KVM"
    ls -l /dev/kvm
    grep -H . /sys/module/kvm_*/parameters/nested
    lscpu | grep -i -E 'virtuali|hypervisor|model name|^cpu\(s\)'
    section "firewall"; sudo nft list ruleset; sudo iptables -S; sudo iptables -S -t nat
    section "load"; uptime; free -h; df -h /var/snap/lxd/common
    section "lxd log"; sudo snap logs -n 200 lxd
}

echo "runner: ${RUNNER_NAME:-unknown} host: $(hostname)"
echo "image: $image plan: $plan storage: $storage"
start=$SECONDS
lxc launch "$image" "$name" --vm -t "$plan" -d "root,size=$storage" || exit 1
echo "launched after $((SECONDS - start))s"

if wait_address; then
    lxc delete -f "$name"
    exit 0
fi

echo "::error::$name got no IPv4 address within ${timeout}s on ${RUNNER_NAME:-unknown}"
dump

section "retry after restarting lxd"
sudo snap restart lxd
sudo lxd waitready --timeout 120
if wait_address; then
    echo "::warning::address appeared after restarting lxd"
else
    section "lxd restart did not help; DHCP leases now"
    lxc network list-leases lxdbr0
fi
lxc delete -f "$name"
exit 1
