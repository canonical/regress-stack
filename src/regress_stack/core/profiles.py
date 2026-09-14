# Copyright 2026 - Canonical Ltd
# SPDX-License-Identifier: GPL-3.0-only

"""Fixed service placement for explicit deployments, independent of legacy discovery."""

from dataclasses import dataclass
import importlib

import networkx as nx

from regress_stack.core import apt
from regress_stack.core.deployment import Context


@dataclass(frozen=True)
class LocalModule:
    name: str
    local_dependencies: tuple[str, ...] = ()
    shared_dependencies: tuple[str, ...] = ()
    package_override: tuple[str, ...] = ()

    @property
    def module(self):
        return importlib.import_module(f"regress_stack.modules.{self.name}")

    def packages(self, no_tempest: bool = False) -> list[str]:
        if self.package_override:
            return list(self.package_override)
        module = self.module
        if hasattr(module, "determine_packages"):
            return module.determine_packages(no_tempest=no_tempest)
        return list(module.PACKAGES)


# Every controller has the same service placement. Dependency edges describe
# work on this machine; shared dependencies never pull in local server packages.
CONTROLLER_MODULES = (
    LocalModule("utils"),
    LocalModule("mysql", ("utils",)),
    LocalModule("rabbitmq", ("utils",)),
    LocalModule("ceph", ("utils",)),
    LocalModule("ovn", ("utils",)),
    LocalModule("keystone", ("mysql",)),
    LocalModule("barbican", ("keystone", "mysql", "rabbitmq")),
    LocalModule("glance", ("keystone", "mysql", "ceph", "barbican")),
    LocalModule("placement", ("keystone", "mysql")),
    LocalModule("cinder", ("keystone", "mysql", "rabbitmq", "ceph", "barbican")),
    LocalModule("neutron", ("keystone", "mysql", "rabbitmq", "ovn")),
    LocalModule("nova", ("glance", "placement", "cinder", "neutron")),
    LocalModule("heat", ("nova", "neutron", "mysql", "rabbitmq")),
    LocalModule("magnum", ("heat", "cinder", "glance", "nova")),
    LocalModule("watcher", ("nova", "mysql", "rabbitmq")),
)
COMPUTE_MODULES = (
    LocalModule("utils"),
    LocalModule("ceph", ("utils",), ("ceph",), ("ceph-common",)),
    LocalModule("ovn", ("utils",), ("ovn",), ("openvswitch-switch", "ovn-host")),
    LocalModule(
        "neutron", ("ovn",), ("neutron", "nova"), ("neutron-ovn-metadata-agent",)
    ),
    LocalModule(
        "nova",
        ("ceph", "neutron"),
        ("keystone", "rabbitmq", "placement", "glance", "cinder"),
        ("nova-compute",),
    ),
)


def execution_order(context: Context, check_packages: bool = True) -> list[LocalModule]:
    modules = CONTROLLER_MODULES if context.controller else COMPUTE_MODULES
    by_name = {module.name: module for module in modules}
    graph = nx.DiGraph()
    for module in modules:
        graph.add_node(module.name)
        for dependency in module.local_dependencies:
            if dependency not in by_name:
                raise RuntimeError(
                    f"Missing local dependency {dependency} for {module.name}"
                )
            graph.add_edge(dependency, module.name)
    ordered = [by_name[name] for name in nx.lexicographical_topological_sort(graph)]
    if check_packages:
        # Unlike legacy package discovery, an explicit profile fails closed.
        # Silently omitting a missing member would produce a different topology.
        missing = [
            package
            for package in packages(context)
            if not apt.pkgs_installed([package])
        ]
        if missing:
            raise RuntimeError("Missing local packages: " + ", ".join(missing))
    return ordered


def packages(context: Context, no_tempest: bool = True) -> list[str]:
    required = ["crudini", "python3-openstackclient"]
    for module in execution_order(context, check_packages=False):
        required.extend(module.packages(no_tempest=no_tempest))
    if context.controller:
        from regress_stack.multinode import coordination

        required.extend(coordination.packages(context))
        required.append("haproxy")
        if context.deployment.profile == "hyperconverged":
            required.append("keepalived")
    if context.controller and not no_tempest:
        required.extend(("tempest", "python3-tempestconf"))
    return list(dict.fromkeys(required))
