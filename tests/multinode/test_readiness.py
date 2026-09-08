# Copyright 2026 - Canonical Ltd
# SPDX-License-Identifier: GPL-3.0-only

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
