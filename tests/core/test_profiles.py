# Copyright 2026 - Canonical Ltd
# SPDX-License-Identifier: GPL-3.0-only

import uuid

import pytest

from regress_stack.core.deployment import Context, Deployment, Node, ProviderNetwork
from regress_stack.core import profiles


@pytest.fixture
def deployment():
    return Deployment(
        "hyperconverged",
        tuple(Node(f"node{i}", f"192.0.2.{i}", "ens3", "ens4") for i in range(1, 4)),
        (Node("compute1", "192.0.2.4", "ens3", "ens4"),),
        "192.0.2.10",
        "192.0.2.0/24",
        ProviderNetwork(
            "198.51.100.0/24", "198.51.100.1", "198.51.100.20", "198.51.100.80"
        ),
    )


def test_compute_has_only_local_client_packages(deployment):
    context = Context(deployment, "compute1", str(uuid.uuid4()))
    packages = profiles.packages(context)
    assert "nova-compute" in packages
    assert "ovn-host" in packages
    assert "neutron-ovn-metadata-agent" in packages
    assert "ceph-common" in packages
    assert not set(packages) & {
        "mysql-server",
        "rabbitmq-server",
        "keystone",
        "ovn-central",
        "nova-api",
        "ceph-mon",
    }
    modules = profiles.execution_order(context, check_packages=False)
    assert [module.name for module in modules].index("neutron") < [
        module.name for module in modules
    ].index("nova")
    nova = next(module for module in modules if module.name == "nova")
    assert "rabbitmq" in nova.shared_dependencies
    assert "rabbitmq" not in nova.local_dependencies


def test_explicit_profile_fails_on_missing_local_package(deployment, monkeypatch):
    context = Context(deployment, "compute1", str(uuid.uuid4()))
    monkeypatch.setattr(
        profiles.apt, "pkgs_installed", lambda packages: "nova-compute" not in packages
    )
    with pytest.raises(RuntimeError, match="Missing local packages: nova-compute"):
        profiles.execution_order(context)


def test_controllers_have_identical_package_requirements(deployment):
    contexts = [
        Context(deployment, node.name, str(uuid.uuid4()))
        for node in deployment.controllers
    ]
    required = [profiles.packages(context) for context in contexts]
    assert required[0] == required[1] == required[2]
    assert {
        "mysql-server",
        "rabbitmq-server",
        "ovn-central",
        "ceph-mon",
        "nova-compute",
        "haproxy",
        "keepalived",
    } <= set(required[0])
