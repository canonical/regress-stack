#!/bin/bash
# Copyright 2026 - Canonical Ltd
# SPDX-License-Identifier: GPL-3.0-only
set -Eeuo pipefail
umask 077
cd /root/regress-stack/src

case "$1" in
    install)
        node=$2
        export DEBIAN_FRONTEND=noninteractive
        policy=/usr/sbin/policy-rc.d
        saved=/run/regress-policy-rc.d
        had_policy=false
        if [[ -e "$policy" || -L "$policy" ]]; then
            cp -a "$policy" "$saved"
            had_policy=true
        fi
        restore_policy() {
            rm -f "$policy"
            if "$had_policy"; then
                mv "$saved" "$policy"
            fi
        }
        trap restore_policy EXIT
        rm -f "$policy"
        printf '#!/bin/sh\nexit 101\n' > "$policy"
        chmod 0755 "$policy"
        apt-get update
        apt-get install -y python3-apt python3-networkx python3-pyroute2 \
            python3-openstackclient python3-yaml python3-click crudini
        # Only stdout contains package names. CLI diagnostics stay on stderr.
        packages=$(python3 -m regress_stack packages \
            --inventory /root/inventory.json --node "$node")
        read -r -a package_names <<< "$packages"
        ((${#package_names[@]} > 0))
        for package in "${package_names[@]}"; do
            if [[ ! "$package" =~ ^[a-z0-9][a-z0-9+.-]*$ ]]; then
                echo "Invalid package name emitted by regress-stack" >&2
                exit 1
            fi
        done
        apt-get install -y "${package_names[@]}"
        test -e /dev/kvm
        # Keep unconfigured package listeners away from the bootstrap VIP.
        systemctl stop apache2 rabbitmq-server
        ;;
    setup)
        python3 -m regress_stack setup --inventory /root/inventory.json \
            --node node1 --export-preseeds /root/preseeds
        ;;
    join)
        python3 -m regress_stack setup --preseed /root/preseed.json
        rm -f /root/preseed.json
        ;;
    ready)
        python3 -m regress_stack ready
        ;;
    test)
        python3 -m regress_stack test \
            --state /var/lib/regress-stack/multinode/context.json --concurrency 2
        ;;
    *)
        echo "Unknown guest phase" >&2
        exit 2
        ;;
esac
