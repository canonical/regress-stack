# Copyright 2026 - Canonical Ltd
# SPDX-License-Identifier: GPL-3.0-only

"""Fixed service placement for explicit deployments, independent of legacy discovery."""

from __future__ import annotations

from collections.abc import Iterator
import dataclasses
import importlib
import pkgutil
from types import ModuleType

import networkx as nx

from regress_stack import modules as service_modules
from regress_stack.core import apt
from regress_stack.core.deployment import Context, Deployment
from regress_stack.multinode import coordination
from regress_stack.roles import Role

# Inventories may disable only these modules, together with their dependents.
OPTIONAL_MODULES = frozenset(
    {"ceph", "cinder", "barbican", "heat", "magnum", "watcher"}
)


@dataclasses.dataclass(frozen=True)
class LocalModule:
    """A discovered service module and its role on this machine."""

    name: str
    role: Role

    @property
    def module(self) -> ModuleType:
        return importlib.import_module(f"regress_stack.modules.{self.name}")

    def packages(self, context: Context, no_tempest: bool = False) -> list[str]:
        selection = self.role.packages
        if callable(selection):
            return list(selection(context, no_tempest))
        if selection is not None:
            return list(selection)
        module = self.module
        if determine := getattr(module, "determine_packages", None):
            return list(determine(no_tempest=no_tempest))
        return list(module.PACKAGES)


def _modules() -> Iterator[tuple[str, dict[str, Role]]]:
    """Discover role declarations through the existing service package."""
    for entry in pkgutil.iter_modules(service_modules.__path__):
        module = importlib.import_module(f"{service_modules.__name__}.{entry.name}")
        if roles := getattr(module, "EXPLICIT_ROLES", None):
            yield entry.name, roles


def validate(deployment: Deployment) -> None:
    """Reject disabled modules that other enabled modules require."""
    disabled = set(deployment.disabled_modules)
    if not disabled <= OPTIONAL_MODULES:
        raise ValueError("Unsupported disabled module")
    for name, roles in _modules():
        if name in disabled:
            continue
        for role in roles.values():
            for dependency in role.requires:
                if dependency in disabled:
                    raise ValueError(
                        f"Disabling {dependency} requires disabling {name}"
                    )


def local_modules(context: Context) -> list[LocalModule]:
    """The enabled local modules in dependency order."""
    role_name = "controller" if context.is_controller else "compute"
    disabled = set(context.deployment.disabled_modules)
    by_name = {
        name: LocalModule(name, roles[role_name])
        for name, roles in _modules()
        if name not in disabled and role_name in roles
    }
    graph: nx.DiGraph[str] = nx.DiGraph()
    for module in by_name.values():
        graph.add_node(module.name)
        for dependency in module.role.requires:
            if dependency not in by_name:
                raise RuntimeError(
                    f"Missing local dependency {dependency} for {module.name}"
                )
            graph.add_edge(dependency, module.name)
        for dependency in module.role.after:
            if dependency in by_name:
                graph.add_edge(dependency, module.name)
    return [by_name[name] for name in nx.lexicographical_topological_sort(graph)]


def packages(context: Context, no_tempest: bool = True) -> list[str]:
    required = ["crudini", "python3-openstackclient"]
    for module in local_modules(context):
        required.extend(module.packages(context, no_tempest=no_tempest))
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
