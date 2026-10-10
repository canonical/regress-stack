# Copyright 2026 - Canonical Ltd
# SPDX-License-Identifier: GPL-3.0-only

"""Fixed service placement for explicit deployments, independent of legacy discovery."""

from __future__ import annotations

from collections.abc import Callable
import dataclasses
import importlib
from types import ModuleType

import networkx as nx

from regress_stack.core import apt
from regress_stack.core.deployment import Context, Deployment, Profile
from regress_stack.multinode import coordination, services

# Inventories may disable only these modules, together with their dependents.
OPTIONAL_MODULES = frozenset(
    {"ceph", "cinder", "barbican", "heat", "magnum", "watcher"}
)


@dataclasses.dataclass(frozen=True)
class LocalModule:
    """A service module configured on this machine.

    requires names local modules that must be enabled. after names local
    modules that are ordered first only when enabled. Shared dependencies are
    services provided by other machines; they never pull in local packages.
    """

    name: str
    requires: tuple[str, ...] = ()
    after: tuple[str, ...] = ()
    shared_dependencies: tuple[str, ...] = ()
    package_override: tuple[str, ...] = ()
    packages_function: str = "determine_packages"
    # None when a multinode recipe configures the service before modules run.
    configure: Callable[[str], None] | None = services.configure

    @property
    def module(self) -> ModuleType:
        return importlib.import_module(f"regress_stack.modules.{self.name}")

    @property
    def local_dependencies(self) -> tuple[str, ...]:
        return self.requires + self.after

    def packages(self, no_tempest: bool = False) -> list[str]:
        if self.package_override:
            return list(self.package_override)
        module = self.module
        if determine := getattr(module, self.packages_function, None):
            return list(determine(no_tempest=no_tempest))
        return list(module.PACKAGES)


# Every controller has the same service placement.
CONTROLLER_MODULES: tuple[LocalModule, ...] = (
    LocalModule("utils", configure=None),
    LocalModule("mysql", ("utils",), configure=None),
    LocalModule("rabbitmq", ("utils",), configure=None),
    LocalModule("ceph", ("utils",), configure=None),
    LocalModule("ovn", ("utils",), configure=None),
    LocalModule("keystone", ("mysql",)),
    LocalModule("barbican", ("keystone", "mysql", "rabbitmq")),
    LocalModule("glance", ("keystone", "mysql"), ("ceph", "barbican")),
    LocalModule("placement", ("keystone", "mysql")),
    LocalModule("cinder", ("keystone", "mysql", "rabbitmq", "ceph"), ("barbican",)),
    LocalModule("neutron", ("keystone", "mysql", "rabbitmq", "ovn")),
    LocalModule("nova", ("glance", "placement", "neutron"), ("cinder",)),
    LocalModule("heat", ("nova", "neutron", "mysql", "rabbitmq")),
    LocalModule("magnum", ("heat", "cinder", "glance", "nova")),
    LocalModule("watcher", ("nova", "mysql", "rabbitmq")),
)
COMPUTE_MODULES: tuple[LocalModule, ...] = (
    LocalModule("utils", configure=None),
    LocalModule(
        "ceph",
        ("utils",),
        shared_dependencies=("ceph",),
        package_override=("ceph-common",),
        configure=None,
    ),
    LocalModule(
        "ovn",
        ("utils",),
        shared_dependencies=("ovn",),
        package_override=("openvswitch-switch", "ovn-host"),
        configure=None,
    ),
    LocalModule(
        "neutron",
        ("ovn",),
        shared_dependencies=("neutron", "nova"),
        package_override=("neutron-ovn-metadata-agent",),
        configure=services.configure_metadata_agent,
    ),
    LocalModule(
        "nova",
        ("neutron",),
        ("ceph",),
        shared_dependencies=("keystone", "rabbitmq", "placement", "glance", "cinder"),
        packages_function="compute_packages",
    ),
)
# Control-profile controllers run the Nova control plane without a hypervisor.
NOVA_CONTROL_PACKAGES = (
    "nova-api",
    "nova-conductor",
    "nova-scheduler",
    "nova-spiceproxy",
    "spice-html5",
)


def validate(deployment: Deployment) -> None:
    """Reject disabled modules that other enabled modules require."""
    disabled = set(deployment.disabled_modules)
    if not disabled <= OPTIONAL_MODULES:
        raise ValueError("Unsupported disabled module")
    for module in CONTROLLER_MODULES + COMPUTE_MODULES:
        if module.name in disabled:
            continue
        for dependency in module.requires:
            if dependency in disabled:
                raise ValueError(
                    f"Disabling {dependency} requires disabling {module.name}"
                )


def local_modules(context: Context) -> list[LocalModule]:
    """The enabled local modules in dependency order."""
    modules = CONTROLLER_MODULES if context.is_controller else COMPUTE_MODULES
    if context.is_controller and context.deployment.profile is Profile.CONTROL:
        modules = tuple(
            dataclasses.replace(module, package_override=NOVA_CONTROL_PACKAGES)
            if module.name == "nova"
            else module
            for module in modules
        )
    disabled = set(context.deployment.disabled_modules)
    by_name = {module.name: module for module in modules if module.name not in disabled}
    graph: nx.DiGraph[str] = nx.DiGraph()
    for module in by_name.values():
        graph.add_node(module.name)
        for dependency in module.requires:
            if dependency not in by_name:
                raise RuntimeError(
                    f"Missing local dependency {dependency} for {module.name}"
                )
            graph.add_edge(dependency, module.name)
        for dependency in module.after:
            if dependency in by_name:
                graph.add_edge(dependency, module.name)
    return [by_name[name] for name in nx.lexicographical_topological_sort(graph)]


def packages(context: Context, no_tempest: bool = True) -> list[str]:
    required = ["crudini", "python3-openstackclient"]
    for module in local_modules(context):
        required.extend(module.packages(no_tempest=no_tempest))
    if context.is_controller:
        required.extend(coordination.packages(context))
        required.append("haproxy")
        if context.deployment.highly_available:
            required.append("keepalived")
    if context.is_controller and not no_tempest:
        required.extend(("tempest", "python3-tempestconf"))
    return list(dict.fromkeys(required))


def verify_installed(context: Context) -> None:
    # Unlike legacy package discovery, an explicit profile fails closed.
    # Silently omitting a missing member would produce a different topology.
    missing = [
        package for package in packages(context) if not apt.pkgs_installed([package])
    ]
    if missing:
        raise RuntimeError("Missing local packages: " + ", ".join(missing))
