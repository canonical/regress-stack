# Copyright 2026 - Canonical Ltd
# SPDX-License-Identifier: GPL-3.0-only

from unittest.mock import Mock

import pytest

from regress_stack.core.deployment import activate
from regress_stack.multinode import (
    access,
    common,
    database,
    messaging,
    networking,
    storage,
)


def test_gr_config_never_persists_bootstrap(context):
    config = database.configuration(context)
    assert "group_replication_bootstrap_group=OFF" in config
    assert "super_read_only=ON" in config
    assert (
        "group_replication_group_seeds=192.0.2.1:33061,192.0.2.2:33061,192.0.2.3:33061"
        in config
    )


@pytest.mark.parametrize("bootstrap", [True, False])
def test_group_start_sequence(context, peer, monkeypatch, bootstrap):
    context = context if bootstrap else peer
    calls = []
    monkeypatch.setattr(common, "done", lambda _: False)
    for name in ("write", "restart", "mark", "wait_for"):
        monkeypatch.setattr(common, name, Mock())
    monkeypatch.setattr(common, "sql", lambda sql: calls.append(sql))
    with activate(context):
        database.setup()
    assert "START GROUP_REPLICATION;" in calls
    assert ("SET GLOBAL group_replication_bootstrap_group=ON;" in calls) == bootstrap
    assert calls[-1] == "SET PERSIST group_replication_start_on_boot=ON;"
    if bootstrap:
        assert calls.index(
            "SET GLOBAL group_replication_bootstrap_group=OFF;"
        ) > calls.index("START GROUP_REPLICATION;")


def test_failed_start_always_disables_bootstrap(context, monkeypatch):
    calls = []

    def sql(statement):
        calls.append(statement)
        if statement == "START GROUP_REPLICATION;":
            raise RuntimeError("failed")

    monkeypatch.setattr(common, "sql", sql)
    monkeypatch.setattr(common, "done", lambda _: False)
    monkeypatch.setattr(common, "write", Mock())
    monkeypatch.setattr(common, "restart", Mock())
    with activate(context), pytest.raises(RuntimeError):
        database.setup()
    assert calls[-1] == "SET GLOBAL group_replication_bootstrap_group=OFF;"


def test_join_grows_existing_quorum_queues(peer, monkeypatch):
    monkeypatch.setattr(common, "done", lambda _: False)
    for name in ("write", "restart", "mark"):
        monkeypatch.setattr(common, name, Mock())
    run = Mock()
    monkeypatch.setattr(common, "run", run)
    with activate(peer):
        messaging.setup()
    run.assert_any_call("rabbitmqctl", ["join_cluster", "rabbit@node1"])
    run.assert_any_call("rabbitmq-queues", ["grow", "rabbit@node2", "all"])


def test_ovn_join_addresses_and_local_socket(peer):
    config = networking.central_options(peer)
    assert "--db-nb-cluster-local-addr=192.0.2.2" in config
    assert "--db-nb-cluster-remote-addr=192.0.2.1" in config
    assert "tcp:192.0.2.3:6642" in config


def test_storage_replicates_across_hosts(context):
    config = storage.configuration(context)
    assert "osd pool default size = 3" in config
    assert "osd pool default min size = 2" in config
    assert "osd crush chooseleaf type = 1" in config


def test_external_check_is_scoped_to_mysql(context):
    config = access.configuration(context)
    defaults, backends = config.split("listen mysql", 1)
    mysql_backend, api_backends = backends.split("listen api-", 1)
    assert "option external-check" not in defaults
    assert "option external-check" in mysql_backend
    assert "option external-check" not in api_backends


def test_haproxy_helper_directory_is_traversable(context, tmp_path, monkeypatch):
    helpers = tmp_path / "helpers"
    helpers.mkdir(mode=0o700)
    monkeypatch.setattr(access, "Path", lambda _: helpers)
    monkeypatch.setattr(common, "write", Mock())
    monkeypatch.setattr(common, "run", Mock())
    monkeypatch.setattr(common, "restart", Mock())
    with activate(context):
        access.setup()
    assert helpers.stat().st_mode & 0o777 == 0o755


def test_ceph_join_selects_management_network(context):
    config = storage.configuration(context)
    assert f"public network = {context.deployment.management_cidr}" in config
    assert "auth allow insecure global_id reclaim = false" in config


def test_ovn_hostname_matches_inventory(context, monkeypatch):
    monkeypatch.setattr(common, "done", lambda _: True)
    monkeypatch.setattr(common, "write", Mock())
    monkeypatch.setattr(common, "restart", Mock())
    run = Mock()
    monkeypatch.setattr(common, "run", run)
    with activate(context):
        networking.setup()
    settings = next(
        call.args[1]
        for call in run.call_args_list
        if call.args[0] == "ovs-vsctl" and call.args[1][0] == "set"
    )
    assert f"external_ids:hostname={context.local.name}" in settings
    assert f"external_ids:system-id={context.local.name}" in settings


def test_database_proxy_timeout_exceeds_pool_recycle(context):
    mysql_backend = (
        access.configuration(context)
        .split("listen mysql", 1)[1]
        .split("listen api-", 1)[0]
    )
    assert "timeout client 3h" in mysql_backend
    assert "timeout server 3h" in mysql_backend


def test_compute_only_removes_gateway_option(compute, monkeypatch):
    monkeypatch.setattr(common, "write", Mock())
    monkeypatch.setattr(common, "restart", Mock())
    run = Mock()
    monkeypatch.setattr(common, "run", run)
    with activate(compute):
        networking.setup()
    run.assert_any_call(
        "ovs-vsctl", ["remove", "Open_vSwitch", ".", "external_ids", "ovn-cms-options"]
    )
    for call in run.call_args_list:
        assert "external_ids:ovn-cms-options=" not in call.args[1]


def test_provider_links_are_activated_on_boot(context, monkeypatch):
    write = Mock()
    restart = Mock()
    run = Mock()
    monkeypatch.setattr(common, "write", write)
    monkeypatch.setattr(common, "restart", restart)
    monkeypatch.setattr(common, "run", run)
    with activate(context):
        networking.provider_links()
    unit = write.call_args.args[1]
    assert "After=openvswitch-switch.service" in unit
    assert "WantedBy=multi-user.target" in unit
    assert "ExecStart=/usr/sbin/ip link set br-ex up" in unit
    assert "ExecStart=/usr/sbin/ip link set ens4 up" in unit
    run.assert_called_once_with("systemctl", ["daemon-reload"])
    restart.assert_called_once_with("regress-stack-provider")


def test_database_failover_closes_sessions_to_unhealthy_primary(context):
    database_listener = (
        access.configuration(context)
        .split("listen mysql", 1)[1]
        .split("listen api-", 1)[0]
    )
    servers = [
        line
        for line in database_listener.splitlines()
        if line.strip().startswith("server ")
    ]
    assert len(servers) == 3
    assert all("on-marked-down shutdown-sessions" in line for line in servers)


def test_group_replication_syncs_each_relay_log_event(context):
    assert "sync_relay_log=1" in database.configuration(context)


def test_new_osd_has_capacity_for_multinode_images(tmp_path, monkeypatch):
    from regress_stack.multinode import storage

    state = tmp_path / "state"
    state.mkdir()
    monkeypatch.setattr(common, "STATE", state)
    monkeypatch.setattr(common, "done", lambda name: False)
    monkeypatch.setattr(common, "mark", Mock())
    monkeypatch.setattr(common, "restart", Mock())
    monkeypatch.setattr(storage, "Path", lambda value: tmp_path / "osd")
    monkeypatch.setattr(
        common, "write", lambda path, data, **kwargs: path.write_text(data)
    )
    calls = []

    def run(command, args, **kwargs):
        calls.append((command, args))
        return "0" if command == "ceph" else "ceph-key"

    monkeypatch.setattr(common, "run", run)
    storage.setup_osd(0)
    assert (
        "fallocate",
        ["--length", "10G", str(tmp_path / "osd" / "backing.img")],
    ) in calls


def test_database_capacity_covers_three_controller_clients(context):
    assert "max_connections=500\n" in database.configuration(context)


def test_wait_for_retries_failed_private_commands(monkeypatch):
    from regress_stack.core.quiet import CommandError

    attempts = []

    def check():
        attempts.append(None)
        if len(attempts) == 1:
            raise CommandError(1, ["ceph"])
        return True

    monkeypatch.setattr(common.time, "sleep", lambda _: None)
    common.wait_for(check, "test")
    assert len(attempts) == 2
