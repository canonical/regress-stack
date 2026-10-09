# Copyright 2026 - Canonical Ltd
# SPDX-License-Identifier: GPL-3.0-only

from unittest.mock import Mock

from regress_stack.multinode.readiness import queue_replicas_ready


def test_requires_replicated_durable_queues_with_live_majority():
    durable = dict(
        type="quorum", durable=True, members=[1, 2, 3], online=[1, 2], arguments={}
    )
    transient = dict(
        type="classic",
        durable=False,
        members=[],
        online=[],
        arguments={"x-expires": 1800000},
    )
    assert queue_replicas_ready([durable, transient], 3)
    assert not queue_replicas_ready([transient], 3)
    for broken in [
        {**durable, "type": "classic"},
        {**durable, "members": [1, 2]},
        {**durable, "online": [1]},
    ]:
        assert not queue_replicas_ready([broken, transient], 3)
    assert not queue_replicas_ready([durable, {**transient, "arguments": {}}], 3)


def test_rabbitmq_cli_typed_arguments():
    durable = dict(
        type="quorum", durable=True, members=[1, 2, 3], online=[1, 2, 3], arguments=[]
    )
    transient = dict(
        type="classic",
        durable=False,
        members="",
        online="",
        arguments=[["x-expires", "signedint", 1800000]],
    )
    assert queue_replicas_ready([durable, transient], 3)
    assert not queue_replicas_ready([durable, {**transient, "arguments": []}], 3)


def test_chassis_check_can_reach_remote_leader(context, monkeypatch):
    import json
    from regress_stack.multinode import readiness

    def run(command, args):
        if command == "ovn-sbctl" and (
            "--db=tcp:192.0.2.1:6642,tcp:192.0.2.2:6642,tcp:192.0.2.3:6642" in args
        ):
            return json.dumps(
                {"data": [[n.name, n.name] for n in context.deployment.nodes]}
            )
        raise RuntimeError("Local database is not the leader")

    monkeypatch.setattr(readiness.common, "run", run)
    monkeypatch.setattr(readiness.common, "sql", lambda *_: "")
    monkeypatch.setattr("regress_stack.modules.keystone.o7k", lambda: None)
    failures = readiness.check(context)
    assert "OVN compute host identities" not in failures


def test_control_readiness_distinguishes_gateways_from_compute(context, monkeypatch):
    import dataclasses
    import json
    from unittest.mock import Mock

    from regress_stack.multinode import readiness

    control = dataclasses.replace(
        context, deployment=dataclasses.replace(context.deployment, profile="control")
    )
    chassis = [[node.name, node.name] for node in control.deployment.nodes]

    def run(command, args):
        if command == "ovn-sbctl" and "Chassis" in args:
            return json.dumps({"data": chassis})
        raise RuntimeError("Unrelated live check")

    client = Mock()
    client.compute.services.return_value = [
        Mock(host="compute1", state="up", status="enabled")
    ]
    monkeypatch.setattr(readiness.common, "run", run)
    monkeypatch.setattr("regress_stack.modules.keystone.o7k", lambda: client)
    failures = readiness.check(control)
    assert "OVN compute host identities" not in failures
    assert "Nova compute registration" not in failures


def test_cinder_services_use_version_independent_rest_api():
    from unittest.mock import Mock
    from regress_stack.multinode.readiness import volume_hosts

    response = Mock()
    response.json.return_value = {
        "services": [
            {"host": "node1@ceph", "state": "up", "status": "enabled"},
            {"host": "node2@ceph", "state": "down", "status": "enabled"},
            {"host": "node3@ceph", "state": "up", "status": "disabled"},
        ]
    }
    proxy = Mock(spec=["get"])
    proxy.get.return_value = response
    assert volume_hosts(proxy) == {"node1"}
    proxy.get.assert_called_once_with(
        "/os-services", params={"binary": "cinder-volume"}
    )
    response.raise_for_status.assert_called_once_with()


def test_storage_free_readiness_omits_ceph_and_cinder(context, monkeypatch):
    import dataclasses
    from unittest.mock import Mock

    from regress_stack.multinode import readiness

    control = dataclasses.replace(
        context,
        deployment=dataclasses.replace(
            context.deployment,
            profile="control",
            disabled_modules=(
                "ceph",
                "cinder",
                "barbican",
                "heat",
                "magnum",
                "watcher",
            ),
        ),
    )
    client = Mock()
    client.compute.services.return_value = [
        Mock(host="compute1", state="up", status="enabled")
    ]
    monkeypatch.setattr(
        readiness.common, "run", Mock(side_effect=RuntimeError("unrelated check"))
    )
    monkeypatch.setattr("regress_stack.modules.keystone.o7k", lambda: client)
    failures = readiness.check(control)
    assert not any(
        "Ceph" in name or "Cinder" in name or "volume API" in name for name in failures
    )
    client.block_storage.volumes.assert_not_called()


def test_string_expiry_is_not_treated_as_expiring():
    durable = dict(
        type="quorum", durable=True, members=[1, 2, 3], online=[1, 2, 3], arguments={}
    )
    for arguments in ({"x-expires": "1800000"}, [["x-expires", "longstr", "1"]]):
        transient = dict(
            type="classic", durable=False, members=[], online=[], arguments=arguments
        )
        assert not queue_replicas_ready([durable, transient], 3)


def test_failed_check_logs_only_exception_type(context, monkeypatch, caplog):
    from regress_stack.multinode import readiness

    def run(command, args, **kwargs):
        raise KeyError("secret-value")

    monkeypatch.setattr(readiness.common, "run", run)
    monkeypatch.setattr(readiness.common, "sql", run)
    monkeypatch.setattr("regress_stack.modules.keystone.o7k", lambda: None)
    with caplog.at_level("WARNING", logger=readiness.__name__):
        failures = readiness.check(context)
    assert "RabbitMQ membership" in failures
    assert "RabbitMQ membership check raised KeyError" in caplog.text
    assert "secret-value" not in caplog.text


def test_sentinel_agreement_queries_every_live_controller(context, monkeypatch):
    from regress_stack.multinode import readiness

    masters = {"192.0.2.1": "192.0.2.1", "192.0.2.2": "192.0.2.1"}
    monkeypatch.setattr(readiness.common, "run", Mock(side_effect=RuntimeError))
    monkeypatch.setattr(readiness.common, "sql", Mock(side_effect=RuntimeError))
    monkeypatch.setattr("regress_stack.modules.keystone.o7k", lambda: None)
    monkeypatch.setattr(readiness.coordination, "check_quorum", lambda *_: False)
    monkeypatch.setattr(
        readiness.coordination,
        "master_address",
        lambda _context, host: masters[host],
    )
    assert "Sentinel primary agreement" not in readiness.check(context, "node3")
    masters["192.0.2.2"] = "192.0.2.2"
    assert "Sentinel primary agreement" in readiness.check(context, "node3")
