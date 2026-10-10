# Copyright 2026 - Canonical Ltd
# SPDX-License-Identifier: GPL-3.0-only

import uuid

import pytest

from regress_stack.core.deployment import Context, Deployment, Node, ProviderNetwork
from regress_stack.multinode import profiles


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
    from regress_stack.modules import nova, neutron

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
    modules = profiles.local_modules(context)
    assert [module.name for module in modules].index("neutron") < [
        module.name for module in modules
    ].index("nova")
    nova = next(module for module in modules if module.name == "nova")
    assert "rabbitmq" in nova.role.shared_dependencies
    assert "rabbitmq" not in nova.role.requires + nova.role.after
    assert nova.role.configured_by == "module"
    neutron_role = next(module for module in modules if module.name == "neutron")
    assert neutron_role.role.setup is neutron.setup_metadata_agent


def test_explicit_profile_fails_on_missing_local_package(deployment, monkeypatch):
    context = Context(deployment, "compute1", str(uuid.uuid4()))
    monkeypatch.setattr(
        profiles.apt, "pkgs_installed", lambda packages: "nova-compute" not in packages
    )
    with pytest.raises(RuntimeError, match="Missing local packages: nova-compute"):
        profiles.verify_installed(context)


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
        names = {module.name for module in profiles.local_modules(context)}
        assert {"nova", "neutron", "ovn"} <= names
        assert not names & {"heat", "magnum", "watcher"}
        packages = profiles.packages(context)
        assert not any(
            package.startswith(("heat-", "magnum-", "watcher-")) for package in packages
        )


def test_heat_cannot_be_excluded_while_magnum_is_enabled(deployment):
    with pytest.raises(ValueError, match="Disabling heat requires disabling magnum"):
        profiles.validate(
            Deployment(**{**deployment.__dict__, "disabled_modules": ("heat",)})
        )


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
        names = {module.name for module in profiles.local_modules(context)}
        assert not names & set(scoped.disabled_modules)
        assert {"nova", "neutron", "ovn"} <= names
        packages = profiles.packages(context)
        assert not any(
            package.startswith(("ceph-", "cinder-", "barbican-"))
            for package in packages
        )
        assert "ceph-common" not in packages


def test_ceph_cannot_be_excluded_with_cinder_enabled(deployment):
    with pytest.raises(ValueError, match="Disabling ceph requires disabling cinder"):
        profiles.validate(
            Deployment(**{**deployment.__dict__, "disabled_modules": ("ceph",)})
        )


@pytest.mark.parametrize(
    "disabled",
    [("cinder", "ceph"), ("nova",), ("keystone",), ("unknown",)],
)
def test_only_optional_modules_and_their_dependents_can_be_disabled(
    deployment, disabled
):
    with pytest.raises(ValueError):
        profiles.validate(
            Deployment(**{**deployment.__dict__, "disabled_modules": disabled})
        )


@pytest.mark.parametrize(
    "disabled",
    [
        (),
        ("barbican",),
        ("watcher",),
        ("heat", "magnum"),
        ("ceph", "cinder", "magnum"),
        ("ceph", "cinder", "barbican", "heat", "magnum", "watcher"),
    ],
)
def test_supported_disabled_module_sets(deployment, disabled):
    profiles.validate(
        Deployment(**{**deployment.__dict__, "disabled_modules": disabled})
    )


def test_execution_order_is_unchanged_for_full_controllers(deployment):
    context = Context(
        deployment,
        "node1",
        str(uuid.uuid4()),
        {"coordination/implementation": "valkey"},
    )
    assert [module.name for module in profiles.local_modules(context)] == [
        "utils",
        "ceph",
        "mysql",
        "keystone",
        "ovn",
        "placement",
        "rabbitmq",
        "barbican",
        "cinder",
        "glance",
        "neutron",
        "nova",
        "heat",
        "magnum",
        "watcher",
    ]


@pytest.mark.parametrize("no_tempest", [False, True])
def test_role_packages_use_explicit_context(deployment, monkeypatch, no_tempest):
    import dataclasses

    from regress_stack.core.deployment import activate
    from regress_stack.modules import nova

    control = dataclasses.replace(deployment, profile="control")
    requested = Context(control, "node1", "00000000-0000-0000-0000-000000000001")
    ambient = Context(deployment, "node1", "00000000-0000-0000-0000-000000000001")

    def callback(no_tempest):
        return ["without-tempest" if no_tempest else "with-tempest"]

    monkeypatch.setattr(nova, "determine_packages", callback)
    monkeypatch.setattr(nova, "compute_packages", callback)
    controller = next(m for m in profiles.local_modules(requested) if m.name == "nova")
    assert controller.packages(requested, no_tempest) == list(
        nova.NOVA_CONTROL_PACKAGES
    )
    with activate(ambient):
        assert controller.packages(requested, no_tempest) == list(
            nova.NOVA_CONTROL_PACKAGES
        )
    with activate(requested):
        assert controller.packages(ambient, no_tempest) == callback(no_tempest)
        compute = Context(control, "compute1", "00000000-0000-0000-0000-000000000001")
        module = next(m for m in profiles.local_modules(compute) if m.name == "nova")
        assert module.packages(compute, no_tempest) == callback(no_tempest)


@pytest.mark.parametrize("name", ["node1", "compute1"])
def test_recipe_roles_have_no_module_setup(deployment, name):
    context = Context(deployment, name, "00000000-0000-0000-0000-000000000001")
    modules = profiles.local_modules(context)
    expected = {"utils", "ceph", "ovn"}
    if context.is_controller:
        expected |= {"mysql", "rabbitmq"}
    recipes = {m.name for m in modules if m.role.configured_by == "recipe"}
    assert recipes == expected
    assert all(m.role.setup is None for m in modules if m.name in recipes)


def test_empty_role_package_override_does_not_use_module_defaults(deployment):
    from regress_stack.roles import Role

    module = profiles.LocalModule("utils", Role(packages=()))
    assert (
        module.packages(
            Context(deployment, "node1", "00000000-0000-0000-0000-000000000001")
        )
        == []
    )
