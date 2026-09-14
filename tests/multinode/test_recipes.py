# Copyright 2026 - Canonical Ltd
# SPDX-License-Identifier: GPL-3.0-only

import json
import subprocess
from unittest.mock import Mock

import pytest

from regress_stack.core.deployment import activate
from regress_stack.multinode import (
    access,
    common,
    coordination,
    database,
    messaging,
    networking,
    preseed,
    storage,
)
from regress_stack.modules import keystone, mysql, rabbitmq, utils


def test_private_command_hides_secrets(caplog, monkeypatch):
    def failed(*args, **kwargs):
        raise subprocess.CalledProcessError(
            1, ["mysql", "secret"], "stdout-secret", "stderr-secret"
        )

    monkeypatch.setattr(common.subprocess, "run", failed)
    with pytest.raises(subprocess.CalledProcessError) as error:
        common.run("mysql", ["password-secret"], input="sql-secret")
    assert "secret" not in str(error.value)
    assert error.value.cmd == ["mysql"]
    assert error.value.stdout is None and error.value.stderr is None
    assert "secret" not in caplog.text


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


def test_peer_consumes_credentials_without_creating_resources(peer, monkeypatch):
    fail = Mock(side_effect=AssertionError("join must not create deployment resources"))
    monkeypatch.setattr(common, "sql", fail)
    monkeypatch.setattr(common, "run", fail)
    monkeypatch.setattr(keystone, "o7k", fail)
    with activate(peer):
        assert mysql.ensure_service("nova") == ("nova", "db_secret")
        assert rabbitmq.ensure_service("nova") == ("nova", "rabbit_secret")
        assert keystone.ensure_service_account(
            "nova", "compute", "http://192.0.2.2:8774/v2.1"
        ) == ("nova", "identity_secret")
        utils.bootstrap_sudo("nova-manage", ["db", "sync"])
    fail.assert_not_called()


def test_api_and_database_routing(context):
    config = access.configuration(context)
    assert "bind 192.0.2.10:15000" in config
    assert "bind 127.0.0.1:13306" in config
    for node in context.deployment.controllers:
        assert f"{node.address}:3306 check" in config
        assert f"{node.address}:5000 check" in config
    with activate(context):
        assert (
            utils.endpoint("http://192.0.2.1:5000/v3/") == "http://192.0.2.10:15000/v3/"
        )
        assert (
            utils.endpoint("http://192.0.2.10:15000/v3/")
            == "http://192.0.2.10:15000/v3/"
        )
        assert mysql.connection_string("nova", "nova", "pw").endswith(
            "@127.0.0.1:13306/nova"
        )
        assert (
            rabbitmq.transport_url("nova", "pw")
            == "rabbit://nova:pw@192.0.2.1:5672,nova:pw@192.0.2.2:5672,nova:pw@192.0.2.3:5672/openstack"
        )


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


def test_export_excludes_server_credentials_from_compute(context, tmp_path):
    context.export(tmp_path / "seeds", preseed.contributions(context))
    values = json.loads((tmp_path / "seeds/compute1.json").read_text())["values"]
    assert set(values) == {
        "rabbitmq/nova",
        "keystone/nova",
        "ceph/fsid",
        "ceph/rbd_uuid",
        "ceph/client.volumes",
        "neutron/metadata",
        "cinder/service-type",
    }
    peer_values = json.loads((tmp_path / "seeds/node2.json").read_text())["values"]
    assert "ceph/mgr.node2" in peer_values
    assert "ceph/mgr.node3" not in peer_values


def test_compute_setup_uses_client_credentials_and_only_starts_compute(
    compute, monkeypatch
):
    from regress_stack.modules import nova, barbican

    calls = []
    monkeypatch.setattr(
        nova.module_utils, "cfg_set", lambda path, *args: calls.extend(args)
    )
    monkeypatch.setattr(nova, "virt_type", lambda: "qemu")
    monkeypatch.setattr(nova, "_ensure_questing_compat", Mock())
    monkeypatch.setattr(nova, "ensure_libvirt_ceph_secret", lambda: "rbd-uuid")
    monkeypatch.setattr(barbican, "installed", lambda: False)
    forbidden = Mock(
        side_effect=AssertionError("compute must not access database or run migrations")
    )
    monkeypatch.setattr(mysql, "ensure_service", forbidden)
    monkeypatch.setattr(nova.core_utils, "sudo", forbidden)
    restarted = Mock()
    monkeypatch.setattr(nova.core_utils, "restart_service", restarted)
    with activate(compute):
        nova.setup()
    forbidden.assert_not_called()
    restarted.assert_called_once_with("nova-compute")
    assert not any(section in ("database", "api_database") for section, _, _ in calls)
    assert ("DEFAULT", "host", "compute1") in calls
    assert (
        "os_vif_ovs",
        "ovsdb_connection",
        "unix:/var/run/openvswitch/db.sock",
    ) in calls


def test_generated_recovery_password_fits_mysql_channel(context, monkeypatch):
    monkeypatch.setattr(coordination, "implementation", lambda: "valkey")
    monkeypatch.setattr(coordination, "sentinel_auth_supported", lambda: True)
    monkeypatch.setattr(common, "run", lambda *args, **kwargs: "ceph-key")
    from regress_stack.modules import cinder

    monkeypatch.setattr(cinder, "get_service_type", lambda: "volumev3")
    preseed.generate(context)
    password = context.secret("mysql/recovery")
    assert len(password) == 32
    assert common.token(password) == password


def test_external_check_is_scoped_to_mysql(context):
    config = access.configuration(context)
    defaults, backends = config.split("listen mysql", 1)
    mysql_backend, api_backends = backends.split("listen api-", 1)
    assert "option external-check" not in defaults
    assert "option external-check" in mysql_backend
    assert "option external-check" not in api_backends


def test_stop_unconfigured_services_preserves_completed_modules(monkeypatch):
    from regress_stack.multinode import services

    run = Mock(
        return_value=(
            "nova-compute.service enabled enabled\n"
            "cinder-volume.service enabled enabled\n"
            "rabbitmq-server.service enabled enabled\n"
        )
    )
    monkeypatch.setattr(common, "run", run)
    monkeypatch.setattr(common, "done", lambda name: name == "service-cinder")
    services.stop_unconfigured()
    assert run.call_args.args == ("systemctl", ["stop", "nova-compute.service"])


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


@pytest.mark.parametrize("setup_phase", [True, False])
def test_setup_client_uses_local_apis_and_runtime_uses_vip(
    context, monkeypatch, setup_phase
):
    connect = Mock()
    monkeypatch.setattr(keystone.openstack, "connect", connect)
    monkeypatch.setattr(keystone.os, "environ", {})
    keystone.o7k.cache_clear()
    try:
        with activate(context, bootstrap_only=setup_phase):
            keystone.o7k()
        options = connect.call_args.kwargs
        if setup_phase:
            assert options["auth_url"] == f"http://{context.local.address}:5000/v3/"
            assert (
                options["network_endpoint_override"]
                == f"http://{context.local.address}:9696/v2.0"
            )
        else:
            assert options == {"load_envvars": True}
            assert (
                keystone.os.environ["OS_AUTH_URL"]
                == f"http://{context.deployment.api_address}:15000/v3/"
            )
    finally:
        keystone.o7k.cache_clear()


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


def test_caracal_nova_uses_privileged_ovs_driver(context, monkeypatch):
    from regress_stack.multinode import services

    configure = Mock()
    run = Mock()
    monkeypatch.setattr(common, "run", run)
    monkeypatch.setattr(services.utils, "cfg_set", configure)
    with activate(context):
        services.prepare("nova")
    run.assert_called_once_with("systemctl", ["start", "libvirtd"])
    options = [option for call in configure.call_args_list for option in call.args[1:]]
    assert ("os_vif_ovs", "ovsdb_interface", "vsctl") in options


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


@pytest.mark.parametrize("controller", [True, False])
def test_nova_metadata_compat_respects_local_role(
    context, compute, monkeypatch, controller
):
    from regress_stack.modules import nova

    metadata = Mock()
    rootwrap = Mock()
    monkeypatch.setattr(nova, "_using_sudo_rs", lambda: True)
    monkeypatch.setattr(nova, "_api_runs_under_apache", lambda: True)
    monkeypatch.setattr(nova, "_ensure_metadata_site", metadata)
    monkeypatch.setattr(nova, "_ensure_sudo_rs_rootwrap", rootwrap)
    monkeypatch.setattr(nova.module_utils, "cfg_set", Mock())
    with activate(context if controller else compute):
        nova._ensure_questing_compat()
    rootwrap.assert_called_once()
    if controller:
        metadata.assert_called_once()
    else:
        metadata.assert_not_called()


@pytest.mark.parametrize("supported", [False, True])
def test_preseed_freezes_sentinel_auth(context, monkeypatch, supported):
    from regress_stack.multinode import preseed
    from regress_stack.modules import cinder

    monkeypatch.setattr(coordination, "implementation", lambda: "redis")
    monkeypatch.setattr(coordination, "sentinel_auth_supported", lambda: supported)
    monkeypatch.setattr(coordination.common, "run", lambda *args, **kwargs: "ceph-key")
    monkeypatch.setattr(cinder, "get_service_type", lambda: "volumev3")
    preseed.generate(context)
    password = context.secret("coordination/password")
    assert password
    assert context.secret("coordination/sentinel-password") == (
        password if supported else ""
    )


def test_wsgi_worker_limit_preserves_other_daemons(tmp_path, monkeypatch):
    from regress_stack.multinode import services

    config = tmp_path / "nova-api.conf"
    config.write_text(
        "WSGIDaemonProcess nova-api processes=5 threads=1 user=nova\n"
        " WSGIDaemonProcess nova-metadata user=nova processes=5 threads=1\n"
        "WSGIDaemonProcess neutron-api processes=4 threads=1\n"
        "# WSGIDaemonProcess nova-api processes=5 threads=1\n"
    )
    monkeypatch.setattr(services, "APACHE_CONFIG_DIRS", (tmp_path,))
    services.limit_wsgi_workers("nova")
    assert config.read_text() == (
        "WSGIDaemonProcess nova-api processes=1 threads=1 user=nova\n"
        " WSGIDaemonProcess nova-metadata user=nova processes=1 threads=1\n"
        "WSGIDaemonProcess neutron-api processes=4 threads=1\n"
        "# WSGIDaemonProcess nova-api processes=5 threads=1\n"
    )


def test_controller_preparation_limits_wsgi_workers(context, monkeypatch):
    from regress_stack.multinode import services

    limit = Mock()
    monkeypatch.setattr(services, "limit_wsgi_workers", limit)
    with activate(context):
        services.prepare("placement")
    limit.assert_called_once_with("placement")


def test_compute_preparation_does_not_edit_apache(compute, monkeypatch):
    from regress_stack.multinode import services

    limit = Mock()
    monkeypatch.setattr(services, "limit_wsgi_workers", limit)
    with activate(compute):
        services.prepare("placement")
    limit.assert_not_called()


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


@pytest.mark.parametrize("role", ["context", "compute"])
def test_nova_preparation_starts_libvirt_before_secret_definition(
    role, request, monkeypatch
):
    from regress_stack.multinode import services

    run = Mock()
    monkeypatch.setattr(common, "run", run)
    with activate(request.getfixturevalue(role)):
        services.prepare("nova")
    run.assert_any_call("systemctl", ["start", "libvirtd"])


def test_database_capacity_covers_three_controller_clients(context):
    assert "max_connections=500\n" in database.configuration(context)


def test_keystone_preparation_starts_cache(context, monkeypatch, tmp_path):
    from regress_stack.multinode import services

    for folder in ("fernet-keys", "credential-keys"):
        for number in ("0", "1"):
            context.values[f"keystone/{folder}/{number}"] = "test-key"
    run = Mock()
    monkeypatch.setattr(common, "run", run)
    monkeypatch.setattr(common, "write", Mock())
    monkeypatch.setattr(services, "Path", lambda _: tmp_path)
    monkeypatch.setattr(services.shutil, "chown", Mock())
    with activate(context):
        services.prepare("keystone")
    run.assert_any_call("systemctl", ["start", "memcached"])
