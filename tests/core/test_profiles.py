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
    from regress_stack.modules import nova

    context = Context(deployment, "compute1", str(uuid.uuid4()))
    packages = profiles.packages(context)
    assert "nova-compute" in packages
    assert set(nova.spice_packages()) <= set(packages)
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
        Context(
            deployment,
            node.name,
            str(uuid.uuid4()),
            {"coordination/implementation": "valkey"},
        )
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
        "valkey-server",
        "valkey-sentinel",
    } <= set(required[0])


def test_control_controllers_have_no_compute_package(deployment):
    control = Deployment(**{**deployment.__dict__, "profile": "control"})
    for node in control.controllers:
        context = Context(
            control,
            node.name,
            str(uuid.uuid4()),
            {"coordination/implementation": "valkey"},
        )
        packages = profiles.packages(context)
        assert "nova-api" in packages
        assert "nova-compute" not in packages
        assert "keepalived" in packages
    compute = Context(control, "compute1", str(uuid.uuid4()))
    assert "nova-compute" in profiles.packages(compute)
    single = Deployment(
        **{
            **control.__dict__,
            "controllers": control.controllers[:1],
            "api_address": control.controllers[0].address,
        }
    )
    context = Context(
        single,
        single.bootstrap.name,
        str(uuid.uuid4()),
        {"coordination/implementation": "valkey"},
    )
    assert "nova-compute" not in profiles.packages(context)
    assert "keepalived" not in profiles.packages(context)


def test_optional_services_can_be_excluded_from_hyperconverged_profile(deployment):
    data = {
        **deployment.__dict__,
        "disabled_modules": ("heat", "magnum", "watcher"),
    }
    scoped = Deployment(**data)
    for node in scoped.controllers:
        context = Context(
            scoped,
            node.name,
            str(uuid.uuid4()),
            {"coordination/implementation": "valkey"},
        )
        names = {module.name for module in profiles.execution_order(context, False)}
        assert {"nova", "neutron", "ovn"} <= names
        assert not names & {"heat", "magnum", "watcher"}
        packages = profiles.packages(context)
        assert not any(
            package.startswith(("heat-", "magnum-", "watcher-")) for package in packages
        )


def test_heat_cannot_be_excluded_while_magnum_is_enabled(deployment):
    with pytest.raises(ValueError, match="requires disabling magnum"):
        Deployment(**{**deployment.__dict__, "disabled_modules": ("heat",)})


def test_control_profile_can_omit_storage_and_optional_services(deployment):
    scoped = Deployment(
        **{
            **deployment.__dict__,
            "profile": "control",
            "controllers": deployment.controllers[:1],
            "api_address": deployment.controllers[0].address,
            "disabled_modules": (
                "ceph",
                "cinder",
                "barbican",
                "heat",
                "magnum",
                "watcher",
            ),
        }
    )
    for node in scoped.nodes:
        context = Context(
            scoped,
            node.name,
            str(uuid.uuid4()),
            {"coordination/implementation": "valkey"},
        )
        names = {module.name for module in profiles.execution_order(context, False)}
        assert not names & set(scoped.disabled_modules)
        assert {"nova", "neutron", "ovn"} <= names
        packages = profiles.packages(context)
        assert not any(
            package.startswith(("ceph-", "cinder-", "barbican-"))
            for package in packages
        )
        assert "ceph-common" not in packages


def test_ceph_cannot_be_excluded_with_cinder_enabled(deployment):
    with pytest.raises(ValueError, match="requires disabling cinder"):
        Deployment(**{**deployment.__dict__, "disabled_modules": ("ceph",)})
