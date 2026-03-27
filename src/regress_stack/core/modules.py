# Copyright 2025 - Canonical Ltd
# SPDX-License-Identifier: GPL-3.0-only

import importlib
import importlib.util
import logging
import pathlib
import pkgutil
import types
import typing

import networkx as nx

import regress_stack.core.apt as apt

LOG = logging.getLogger(__name__)
_MOD_REGISTRY: typing.MutableMapping[str, types.ModuleType] = {}


def load_module(name: str, path: str):
    if name in _MOD_REGISTRY:
        return _MOD_REGISTRY[name]
    spec = importlib.util.find_spec(name, path)
    if spec is None:
        raise RuntimeError(f"Module {name} not found!")
    module_loaded = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module_loaded)
    _MOD_REGISTRY[name] = module_loaded
    LOG.debug("Loaded module %r from %r", name, path)
    return module_loaded


class ModuleInfo:
    name: str
    packages: typing.List[str]

    def __init__(self, name: str, module: types.ModuleType):
        self.name = name.rsplit(".")[-1]
        if hasattr(module, "determine_packages"):
            self.packages = module.determine_packages()
        else:
            self.packages = getattr(module, "PACKAGES")

    def __str__(self):
        return f"{self.name} ({' '.join(self.packages)})"


def modules() -> typing.List[str]:
    return list(ModuleInfo(name, module) for name, module in _MOD_REGISTRY.items())


class ModuleComp:
    name: str
    module: types.ModuleType

    def __init__(self, name: str, module: types.ModuleType) -> None:
        self.name = name
        self.module = module

    def __hash__(self) -> int:
        return hash(self.name) ^ hash(self.module.__file__)

    def __eq__(self, value: object) -> bool:
        if not isinstance(value, ModuleComp):
            return False
        if self.name == value.name and self.module.__file__ == value.module.__file__:
            return True
        return False

    def __str__(self) -> str:
        return self.name

    def __lt__(self, other: "ModuleComp") -> bool:
        return self.name < other.name

    def __repr__(self) -> str:
        return f"ModuleComp(name={self.name}, file={self.module.__file__})"


def build_dependency_graph(modules_mod: types.ModuleType) -> nx.DiGraph:
    """Build a directed graph of dependencies."""
    modules_dir = pathlib.Path(modules_mod.__path__[0])
    package = str(modules_mod.__package__)
    modules = list(pkgutil.iter_modules([str(modules_dir)]))
    graph: nx.DiGraph[ModuleComp] = nx.DiGraph()

    for module in modules:
        canonical_name = package + "." + module.name
        module_loaded = load_module(canonical_name, module.module_finder.path)
        mod = ModuleComp(
            canonical_name,
            module_loaded,
        )
        dependencies: set[types.ModuleType] = getattr(
            module_loaded, "DEPENDENCIES", set()
        )
        optional_dependencies: set[types.ModuleType] = getattr(
            module_loaded, "OPTIONAL_DEPENDENCIES", set()
        )
        # In case someone includes a dependency in both DEPENDENCIES and OPTIONAL_DEPENDENCIES
        dependencies = dependencies - optional_dependencies
        if hasattr(module_loaded, "determine_packages"):
            packages = module_loaded.determine_packages()
        else:
            packages = getattr(module_loaded, "PACKAGES", [])

        graph.add_node(mod, installed=apt.pkgs_installed(packages))
        for dep in dependencies:
            graph.add_edge(ModuleComp(dep.__name__, dep), mod, optional=False)
        for dep in optional_dependencies:
            graph.add_edge(ModuleComp(dep.__name__, dep), mod, optional=True)

    return graph


def filter_graph(G: nx.DiGraph) -> nx.DiGraph:
    """Remove nodes with uninstalled packages."""
    # Identify nodes with installed=False
    nodes_to_remove = {
        n for n, data in G.nodes(data=True) if not data.get("installed", False)
    }

    # Identify nodes that are only connected via optional=True edges
    def is_only_optional(n):
        """If all predecessors are optional, then this node is optional.

        If there are no predecessors, then this node is not optional.
        """
        predecessors = list(G.predecessors(n))
        if not predecessors:
            return False

        return all(
            G.get_edge_data(pred, n).get("optional", False) for pred in predecessors
        )

    # Identity nodes missing required dependencies
    def is_missing_required(n, to_remove: set):
        """If predecessor is missing, then this node is missing."""
        predecessors = set(G.predecessors(n))
        if not predecessors:
            return False

        # Only consider required predecessors
        predecessors = {
            pred
            for pred in predecessors
            if not G.get_edge_data(pred, n, {}).get("optional", False)
        }
        return bool(predecessors.intersection(to_remove))

    # Collect nodes to remove
    changed = True
    while changed:
        changed = False
        for node in G.nodes:
            if node not in nodes_to_remove and (
                is_only_optional(node) or is_missing_required(node, nodes_to_remove)
            ):
                nodes_to_remove.add(node)
                changed = True  # Ensure we check again after removals

    LOG.debug("Removing nodes %r", nodes_to_remove)

    G.remove_nodes_from(nodes_to_remove)

    return G


def get_subgraph_to_path(G: nx.DiGraph, target: str) -> nx.DiGraph:
    """Return subgraph to target."""
    subgraph = nx.ancestors(G, target)
    subgraph.add(target)
    return G.subgraph(subgraph)


def get_execution_order(
    modules_mod: types.ModuleType, target=None, filter_missing=True
) -> typing.List[ModuleComp]:
    """Determine the execution order of modules based on dependencies.

    Always include the utils module as the first module.

    Args:
        modules_mod: The modules package to analyze
        target: Optional target module name to limit the scope
        filter_missing: If True, filter out modules with missing dependencies
    """
    LOG.debug("Building dependency graph from %r...", modules_mod.__name__)

    # Load utils module using the same approach as build_dependency_graph
    utils_canonical_name = str(modules_mod.__package__) + ".utils"
    utils_mod = load_module(utils_canonical_name, modules_mod.__path__[0])
    utils = ModuleComp(utils_canonical_name, utils_mod)

    if target == "utils":
        return [utils]

    graph = build_dependency_graph(modules_mod)
    if filter_missing:
        graph = filter_graph(graph)

    if not nx.is_directed_acyclic_graph(graph):
        raise RuntimeError("Circular dependency detected!")

    order = list(nx.lexicographical_topological_sort(graph))
    if utils in order:
        order.remove(utils)
    if not target:
        return [utils] + order

    end_node = None
    for mod in order:
        if mod.name.rsplit(".")[-1] == target:
            end_node = mod
            break
    else:
        raise RuntimeError(f"Target {target!r} not found!")

    sg = get_subgraph_to_path(graph, end_node)
    order = list(nx.lexicographical_topological_sort(sg))
    if utils in order:
        order.remove(utils)
    return [utils] + order
