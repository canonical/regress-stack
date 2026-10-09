# Copyright 2026 - Canonical Ltd
# SPDX-License-Identifier: GPL-3.0-only

import dataclasses
from unittest.mock import Mock

import pytest

from regress_stack import topology
from regress_stack.core import utils as core_utils
from regress_stack.core.deployment import activate
from regress_stack.modules import keystone, mysql, ovn, rabbitmq, utils


@pytest.fixture
def local_ip(monkeypatch):
    monkeypatch.setattr(core_utils, "_my_ip", lambda: "203.0.113.5")
    return "203.0.113.5"


def test_single_node_defaults_match_legacy_values(local_ip):
    assert topology.context() is None
    assert keystone.auth_url() == f"http://{local_ip}:5000/v3/"
    assert topology.service_url(8774, "/v2.1") == f"http://{local_ip}:8774/v2.1"
    assert mysql.get_host() == "localhost"
    assert rabbitmq.transport_url("u", "p") == "rabbit://u:p@localhost:5672/openstack"
    assert ovn.nb_connection() == f"tcp:{local_ip}:6641"
    assert ovn.sb_connection() == f"tcp:{local_ip}:6642"
    assert topology.ovsdb_connection() == f"tcp:{local_ip}:6640"
    assert topology.owns_shared_state() and topology.is_bootstrap_node()
    assert topology.is_controller() and topology.runs_compute()
    assert topology.spice_console_enabled() and topology.distributed_floating_ips()
    assert topology.secret("keystone/admin", "changeme") == "changeme"
    assert topology.preseeded("cinder/service-type") is None
    assert topology.provider_network() is None and topology.node_name() is None
    with pytest.raises(RuntimeError):
        topology.deployment_secret("ceph/rbd_uuid")


def test_single_controller_api_uses_node_address_and_port(context):
    single = dataclasses.replace(
        context,
        deployment=dataclasses.replace(
            context.deployment,
            profile="single",
            controllers=context.deployment.controllers[:1],
            api_address=context.deployment.controllers[0].address,
        ),
    )
    with activate(single):
        assert keystone.auth_url() == "http://192.0.2.1:5000/v3/"


def test_configuration_values_are_written_verbatim(context, monkeypatch):
    run = Mock()
    monkeypatch.setattr(utils.core_utils, "run", run)
    with activate(context):
        utils.cfg_set("/etc/x.conf", ("s", "k", "http://192.0.2.1:5000/v3/"))
    run.assert_called_once_with(
        "crudini", ["--set", "/etc/x.conf", "s", "k", "http://192.0.2.1:5000/v3/"]
    )


def test_compute_role_and_control_profile(context, compute):
    with activate(compute):
        assert not topology.is_controller() and topology.runs_compute()
        assert not topology.is_bootstrap_node()
        assert topology.node_name() == "compute1"
    control = dataclasses.replace(
        context, deployment=dataclasses.replace(context.deployment, profile="control")
    )
    with activate(control):
        assert topology.is_controller() and not topology.runs_compute()
        assert not topology.spice_console_enabled()
        assert not topology.distributed_floating_ips()
