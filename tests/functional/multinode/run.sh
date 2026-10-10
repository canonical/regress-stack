#!/bin/bash
# Copyright 2026 - Canonical Ltd
# SPDX-License-Identifier: GPL-3.0-only
set -Eeuo pipefail
umask 077

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
ROOT=$(cd -- "$SCRIPT_DIR/../../.." && pwd)
phase=${1:-help}
shift || true
release=${MULTINODE_RELEASE:-}
work_dir=${MULTINODE_WORK_DIR:-}
artifacts=${MULTINODE_ARTIFACTS:-}
pool=${MULTINODE_POOL:-}
while (($#)); do
    case "$1" in
        --release) release=$2; shift 2 ;;
        --work-dir) work_dir=$2; shift 2 ;;
        --artifacts) artifacts=$2; shift 2 ;;
        --pool) pool=$2; shift 2 ;;
        *) echo "Unknown option: $1" >&2; exit 2 ;;
    esac
done
if [[ "$phase" == help || -z "$release" || -z "$work_dir" || -z "$artifacts" ]]; then
    echo "Usage: $0 PHASE --release RELEASE --work-dir DIR --artifacts DIR [--pool POOL]" >&2
    echo "Phases: all, prepare, provision, install, setup, ready, test, ready-after, diagnostics, cleanup, check-host" >&2
    exit 2
fi
case "$release" in jammy|noble|resolute) ;; *) echo "Unsupported release" >&2; exit 2 ;; esac
work_dir=$(realpath -m -- "$work_dir")
artifacts=$(realpath -m -- "$artifacts")
if [[ "$work_dir" == "$artifacts" || "$artifacts" == "$work_dir/"* || "$work_dir" == "$artifacts/"* ]]; then
    echo "Private work directory and public artifacts must be separate" >&2
    exit 2
fi
nodes=(node1 node2 node3)
command_pid=
heartbeat_pid=
follower_pid=
project=
record_phase=false

log() { printf '%s [%s] %s\n' "$(date -u +%FT%TZ)" "$phase" "$*"; }
host_lxc() { lxc --force-local --project default "$@"; }
tf() { terraform -chdir="$work_dir/terraform" "$@"; }

preflight() {
    local pools space
    for tool in lxc terraform jq python3 iptables timeout; do command -v "$tool" >/dev/null; done
    test -e /dev/kvm || { log "KVM is required"; return 1; }
    (( $(getconf _NPROCESSORS_ONLN) >= 26 )) || { log "At least 26 host CPUs are required"; return 1; }
    (( $(awk '/MemAvailable:/ {print $2}' /proc/meminfo) >= 80 * 1024 * 1024 )) || {
        log "At least 80 GiB of available host RAM is required"; return 1;
    }
    pools=$(host_lxc storage list --format json)
    if [[ -z "$pool" ]]; then
        [[ $(jq length <<< "$pools") == 1 ]] || { log "Specify --pool when LXD has multiple pools"; return 1; }
        pool=$(jq -r '.[0].name' <<< "$pools")
    fi
    jq -e --arg pool "$pool" 'any(.[]; .name == $pool)' <<< "$pools" >/dev/null
    space=$(lxc --force-local query "/1.0/storage-pools/$(jq -nr --arg p "$pool" '$p|@uri')/resources")
    jq -e '.space.total - .space.used >= 270 * 1024 * 1024 * 1024' <<< "$space" >/dev/null || {
        log "At least 270 GiB of free LXD pool space is required"; return 1;
    }
}

collect_credentials() {
    local node
    mkdir -p "$work_dir/credentials"
    for node in "${nodes[@]}"; do
        if [[ -e "$work_dir/setup-$node" ]]; then
            if ! timeout 30s lxc --force-local --project "$project" exec "$node" -- \
                cat /var/lib/regress-stack/multinode/context.json \
                > "$work_dir/credentials/$node.json" 2>/dev/null; then
                return 1
            fi
        fi
    done
    if [[ -e "$work_dir/test-started" ]]; then
        timeout 30s lxc --force-local --project "$project" exec node1 -- \
            cat /root/regress-stack/src/mycloud01/etc/tempest.conf \
            > "$work_dir/credentials/tempest.conf" 2>/dev/null || return 1
    fi
}

publish() {
    local name=$1 stream options=()
    if [[ "$name" != host-* && -e "$work_dir/setup-node1" ]]; then
        if ! collect_credentials; then
            log "$name: private output withheld because the credential inventory is incomplete"
            return 0
        fi
        options=(--credentials "$work_dir/credentials")
    fi
    for stream in stdout stderr; do
        if [[ ! -s "$work_dir/logs/$name.$stream" ]]; then
            : > "$artifacts/$name.$stream.log"
            continue
        fi
        if ! python3 "$SCRIPT_DIR/sanitize.py" "${options[@]}" \
            < "$work_dir/logs/$name.$stream" > "$work_dir/logs/$name.$stream.filtered"; then
            log "$name/$stream: output withheld because filtering failed"
            continue
        fi
        cp "$work_dir/logs/$name.$stream.filtered" "$artifacts/$name.$stream.log"
        # Test outcomes were already published live; retain the full filtered
        # stdout artifact without replaying every result in the console.
        if [[ "$name" == node1-tempest && "$stream" == stdout ]]; then continue; fi
        while IFS= read -r line || [[ -n "$line" ]]; do
            log "[$name/$stream] $line"
        done < "$artifacts/$name.$stream.log"
    done
}

run() {
    local name=$1 seconds=$2 status=0
    shift 2
    if [[ "$1" == guest ]]; then
        shift
        local node=$1
        shift
        set -- lxc --force-local --project "$project" exec "$node" --mode=non-interactive -- "$@"
    fi
    log "$name: starting"
    if [[ "$name" == node1-tempest ]]; then
        : > "$work_dir/logs/$name.stdout"
        rm -f "$work_dir/logs/$name.done"
        python3 "$SCRIPT_DIR/sanitize.py" --follow "$work_dir/logs/$name.stdout" \
            --done "$work_dir/logs/$name.done" --prefix "[$phase] [$name/stdout] " &
        follower_pid=$!
    fi
    timeout --signal=TERM --kill-after=30s "${seconds}s" "$@" \
        > "$work_dir/logs/$name.stdout" 2> "$work_dir/logs/$name.stderr" &
    command_pid=$!
    (
        timer_pid=
        trap 'if [[ -n "$timer_pid" ]]; then kill "$timer_pid" 2>/dev/null || true; fi' EXIT
        trap 'exit 0' TERM
        while kill -0 "$command_pid" 2>/dev/null; do
            sleep 30 &
            timer_pid=$!
            wait "$timer_pid" || break
            timer_pid=
            if kill -0 "$command_pid" 2>/dev/null; then log "$name: still running"; else break; fi
        done
    ) &
    heartbeat_pid=$!
    wait "$command_pid" || status=$?
    command_pid=
    kill "$heartbeat_pid" 2>/dev/null || true
    wait "$heartbeat_pid" 2>/dev/null || true
    heartbeat_pid=
    if [[ -n "$follower_pid" ]]; then
        touch "$work_dir/logs/$name.done"
        wait "$follower_pid" || log "$name: live result filtering failed"
        follower_pid=
    fi
    publish "$name"
    log "$name: exit status $status"
    return "$status"
}

prepare() {
    # Refuse to reuse Terraform state or expose logs through pre-existing paths.
    [[ ! -e "$work_dir" && ! -e "$artifacts" ]]
    mkdir -m 0700 -- "$work_dir"
    if ! mkdir -m 0700 -- "$artifacts"; then rmdir -- "$work_dir"; return 1; fi
    touch "$work_dir/owned"
    mkdir "$work_dir/logs" "$work_dir/terraform"
    record_phase=true
    preflight
    cp "$SCRIPT_DIR/"*.tf "$SCRIPT_DIR/.terraform.lock.hcl" "$work_dir/terraform/"
    local run_id
    run_id="rs$(tr -d '-' < /proc/sys/kernel/random/uuid | cut -c 1-8)"
    jq -n --arg release "$release" --arg id "$run_id" --arg pool "$pool" \
        '{release: $release, run_id: $id, storage_pool: $pool}' > "$work_dir/terraform/terraform.tfvars.json"
    git -c safe.directory="$ROOT" -C "$ROOT" rev-parse HEAD > "$work_dir/commit"
    git -c safe.directory="$ROOT" -C "$ROOT" ls-files -z -- src pyproject.toml \
        | tar -C "$ROOT" --null -T - -cf "$work_dir/source.tar"
    log "Host prerequisites passed; private Terraform state prepared"
}

forwarding() {
    local action=$1 bridge direction status=0
    local rule
    for bridge in "$(jq -r '.run_id' "$work_dir/terraform/terraform.tfvars.json")-m" \
        "$(jq -r '.run_id' "$work_dir/terraform/terraform.tfvars.json")-p"; do
        for direction in out reply; do
            if [[ "$direction" == out ]]; then
                rule=(-i "$bridge" -j ACCEPT)
            else
                rule=(-o "$bridge" -m conntrack --ctstate "RELATED,ESTABLISHED" -j ACCEPT)
            fi
            if [[ "$action" == add ]]; then
                run "host-forward-$bridge-$direction" 30 iptables -I FORWARD "${rule[@]}"
            elif iptables -C FORWARD "${rule[@]}" >/dev/null 2>&1; then
                run "host-unforward-$bridge-$direction" 30 iptables -D FORWARD "${rule[@]}" || status=$?
            fi
        done
    done
    return "$status"
}

provision() {
    run host-terraform-init 300 terraform -chdir="$work_dir/terraform" init -input=false -lockfile=readonly
    run host-terraform-apply 1800 terraform -chdir="$work_dir/terraform" apply -input=false -auto-approve
    tf output -json inventory > "$artifacts/inventory.json"
    project=$(tf output -raw project)
    forwarding add
    local node
    for node in "${nodes[@]}"; do
        run "$node-cloud-init" 600 guest "$node" cloud-init status --wait
        run "$node-source-dir" 30 guest "$node" mkdir -p /root/regress-stack
        run "$node-source-push" 60 lxc --force-local --project "$project" file push \
            "$work_dir/source.tar" "$node/root/source.tar"
        run "$node-source-unpack" 60 guest "$node" tar xf /root/source.tar -C /root/regress-stack
        run "$node-inventory-push" 30 lxc --force-local --project "$project" file push \
            "$artifacts/inventory.json" "$node/root/inventory.json" --mode 0600
        run "$node-script-push" 30 lxc --force-local --project "$project" file push \
            "$SCRIPT_DIR/guest.sh" "$node/root/multinode-guest.sh" --mode 0700
    done
}

install() {
    local node
    for node in "${nodes[@]}"; do
        run "$node-install" 2400 guest "$node" bash /root/multinode-guest.sh install "$node"
        run "$node-packages" 60 guest "$node" dpkg-query -W
    done
}

setup() {
    local node
    touch "$work_dir/setup-node1"
    run node1-bootstrap 1800 guest node1 bash /root/multinode-guest.sh setup
    for node in node2 node3; do
        # Never publish the file content or transfer it through Terraform.
        run "$node-preseed-pull" 30 lxc --force-local --project "$project" file pull \
            "node1/root/preseeds/$node.json" "$work_dir/$node-preseed.json"
        chmod 0600 "$work_dir/$node-preseed.json"
        run "$node-preseed-push" 30 lxc --force-local --project "$project" file push \
            "$work_dir/$node-preseed.json" "$node/root/preseed.json" --mode 0600
        touch "$work_dir/setup-$node"
        run "$node-join" 1800 guest "$node" bash /root/multinode-guest.sh join
        rm -f "$work_dir/$node-preseed.json"
    done
}

ready() {
    local node attempt=0 status remaining deadline=$((SECONDS + 900)) pending=("${nodes[@]}") next=()
    while ((SECONDS < deadline)); do
        attempt=$((attempt + 1))
        next=()
        for node in "${pending[@]}"; do
            remaining=$((deadline - SECONDS))
            ((remaining > 0)) || { log "Readiness timed out on ${pending[*]}"; return 1; }
            ((remaining <= 180)) || remaining=180
            status=0
            run "$node-$phase-$attempt" "$remaining" guest "$node" bash /root/multinode-guest.sh ready || status=$?
            if ((status != 0)); then next+=("$node"); fi
        done
        ((${#next[@]})) || return 0
        pending=("${next[@]}")
        sleep 10
    done
    log "Readiness timed out on ${pending[*]}"
    return 1
}

diagnostics() {
    local node
    for node in "${nodes[@]}"; do
        # Some diagnostic commands return nonzero when there are failed units.
        run "$node-failed-units" 30 guest "$node" systemctl --failed --no-pager || true
        run "$node-journal" 60 guest "$node" journalctl -n 1000 --no-pager || true
    done
}

cleanup() {
    local status=0
    if [[ -e "$work_dir/terraform/terraform.tfvars.json" ]]; then
        forwarding remove || status=$?
        if [[ -d "$work_dir/terraform/.terraform" ]]; then
            run host-terraform-destroy 600 terraform -chdir="$work_dir/terraform" destroy \
                -input=false -auto-approve || status=$?
        fi
    fi
    # Keep state after failed cleanup so remaining owned resources can be removed.
    if ((status == 0)); then rm -rf -- "$work_dir"; fi
    return "$status"
}

finish() {
    local status=$? cleanup_status=0
    trap - EXIT TERM INT
    if [[ -n "$command_pid" ]]; then
        kill "$command_pid" 2>/dev/null || true
        wait "$command_pid" 2>/dev/null || true
    fi
    if [[ -n "$heartbeat_pid" ]]; then kill "$heartbeat_pid" 2>/dev/null || true; fi
    if [[ -n "$follower_pid" ]]; then
        kill "$follower_pid" 2>/dev/null || true
        wait "$follower_pid" 2>/dev/null || true
    fi
    if "$record_phase"; then
        jq -n --arg phase "$phase" --argjson status "$status" \
            '{phase: $phase, exit_status: $status}' > "$artifacts/phase-$phase.json"
    fi
    if "$record_phase" && [[ "$requested_phase" == all ]]; then
        phase=diagnostics
        if [[ -e "$work_dir/terraform/terraform.tfvars.json" ]]; then diagnostics || true; fi
        phase=cleanup
        cleanup || cleanup_status=$?
        jq -n --argjson status "$cleanup_status" '{phase: "cleanup", exit_status: $status}' \
            > "$artifacts/phase-cleanup.json"
        ((cleanup_status == 0)) || status=$cleanup_status
    fi
    if "$record_phase" && [[ -d "$artifacts" ]]; then
        jq -s --arg release "$release" --arg project "$project" \
            --arg commit "${commit:-unknown}" \
            '{release: $release, project: $project, commit: $commit,
              status: (if all(.[]; .exit_status == 0) and
                any(.[]; .phase == "test") and any(.[]; .phase == "ready-after") and
                any(.[]; .phase == "cleanup") then "passed" else "failed" end),
              phases: .}' "$artifacts/"phase-*.json > "$artifacts/result.json"
    fi
    exit "$status"
}

requested_phase=$phase
if [[ "$phase" == check-host ]]; then preflight; log "Host prerequisites passed"; exit; fi
trap finish EXIT
trap 'exit 143' TERM
trap 'exit 130' INT
if [[ "$phase" != prepare && "$phase" != all ]]; then
    if [[ ! -e "$work_dir/terraform/terraform.tfvars.json" ]]; then
        if [[ "$phase" == diagnostics ]]; then exit 0; fi
        if [[ "$phase" == cleanup ]]; then
            # A failed preflight can leave our empty private directory behind.
            if [[ -e "$work_dir/owned" ]]; then
                record_phase=true
                rm -rf -- "$work_dir"
            fi
            exit 0
        fi
        echo "Run prepare first" >&2; exit 1
    fi
    [[ $(jq -r .release "$work_dir/terraform/terraform.tfvars.json") == "$release" ]]
    project="$(jq -r .run_id "$work_dir/terraform/terraform.tfvars.json")-ci"
    commit=$(cat "$work_dir/commit")
    record_phase=true
fi
case "$phase" in
    all)
        phase=prepare
        prepare
        commit=$(cat "$work_dir/commit")
        printf '{"phase": "prepare", "exit_status": 0}\n' > "$artifacts/phase-prepare.json"
        for phase in provision install setup ready test ready-after; do
            if [[ "$phase" == test ]]; then
                touch "$work_dir/test-started"
                run node1-tempest 3600 guest node1 bash /root/multinode-guest.sh test
            elif [[ "$phase" == ready-after ]]; then ready
            else "$phase"
            fi
            jq -n --arg phase "$phase" '{phase: $phase, exit_status: 0}' > "$artifacts/phase-$phase.json"
        done
        ;;
    prepare) prepare; commit=$(cat "$work_dir/commit") ;;
    provision|install|setup|ready|diagnostics|cleanup) "$phase" ;;
    ready-after) ready ;;
    test)
        touch "$work_dir/test-started"
        run node1-tempest 3600 guest node1 bash /root/multinode-guest.sh test
        ;;
    *) echo "Unknown phase: $phase" >&2; exit 2 ;;
esac
