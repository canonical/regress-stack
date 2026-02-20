# Copyright 2025 - Canonical Ltd
# SPDX-License-Identifier: GPL-3.0-only

from unittest.mock import MagicMock, patch

import networkx as nx
import pytest

from regress_stack.core.modules import ModuleComp, build_dependency_graph, filter_graph


@pytest.fixture
def mock_modules():
    mock_mod_spec = [
        "name",
        "module_finder",
        "__name__",
        "__file__",
        "DEPENDENCIES",
        "OPTIONAL_DEPENDENCIES",
        "PACKAGES",
    ]
    mock_mod1 = MagicMock(spec=mock_mod_spec)
    mock_mod1.name = "mod1"
    mock_mod1.__name__ = "regress_stack.modules.mod1"
    mock_mod1.__file__ = "/fake/path/mod1.py"
    mock_mod1.DEPENDENCIES = set()
    mock_mod1.OPTIONAL_DEPENDENCIES = set()
    mock_mod1.PACKAGES = ["pkg1"]

    mock_mod2 = MagicMock(spec=mock_mod_spec)
    mock_mod2.name = "mod2"
    mock_mod2.__name__ = "regress_stack.modules.mod2"
    mock_mod2.__file__ = "/fake/path/mod2.py"
    mock_mod2.DEPENDENCIES = {mock_mod1}
    mock_mod2.OPTIONAL_DEPENDENCIES = set()
    mock_mod2.PACKAGES = ["pkg2"]

    mock_mod3 = MagicMock(spec=mock_mod_spec)
    mock_mod3.name = "mod3"
    mock_mod3.__name__ = "regress_stack.modules.mod3"
    mock_mod3.__file__ = "/fake/path/mod3.py"
    mock_mod3.DEPENDENCIES = set()
    mock_mod3.OPTIONAL_DEPENDENCIES = {mock_mod1}
    mock_mod3.PACKAGES = ["pkg3"]

    mock_modules_mod = MagicMock(spec=mock_mod_spec)
    mock_modules_mod.__path__ = ["/fake/path"]
    mock_modules_mod.__package__ = "regress_stack.modules"
    mock_modules_mod.mod1 = mock_mod1
    mock_modules_mod.mod2 = mock_mod2
    mock_modules_mod.mod3 = mock_mod3

    return mock_modules_mod


@patch("regress_stack.core.modules.pkgutil.iter_modules")
@patch("regress_stack.core.modules.load_module")
@patch("regress_stack.core.modules.apt.pkgs_installed")
def test_build_dependency_graph(
    mock_pkgs_installed, mock_load_module, mock_iter_modules, mock_modules
):
    mock_iter_modules.return_value = [
        mock_modules.mod1,
        mock_modules.mod2,
        mock_modules.mod3,
    ]

    mock_load_module.side_effect = lambda name, path: getattr(
        mock_modules, name.rsplit(".", 1)[1]
    )
    mock_pkgs_installed.return_value = True

    graph = build_dependency_graph(mock_modules)

    assert isinstance(graph, nx.DiGraph)
    assert len(graph.nodes) == 3
    assert len(graph.edges) == 2

    mod1 = ModuleComp("regress_stack.modules.mod1", mock_modules.mod1)
    mod2 = ModuleComp("regress_stack.modules.mod2", mock_modules.mod2)
    mod3 = ModuleComp("regress_stack.modules.mod3", mock_modules.mod3)

    assert graph.has_node(mod1)
    assert graph.has_node(mod2)
    assert graph.has_node(mod3)

    assert graph.nodes[mod1]["installed"] is True
    assert graph.nodes[mod2]["installed"] is True
    assert graph.nodes[mod3]["installed"] is True

    assert graph.has_edge(mod1, mod2)
    assert graph.has_edge(mod1, mod3)
    assert not graph.has_edge(mod2, mod3)

    assert graph[mod1][mod2]["optional"] is False
    assert graph[mod1][mod3]["optional"] is True


@patch("regress_stack.core.modules.pkgutil.iter_modules")
@patch("regress_stack.core.modules.load_module")
@patch("regress_stack.core.modules.apt.pkgs_installed")
def test_build_dependency_graph_missing_packages(
    mock_pkgs_installed, mock_load_module, mock_iter_modules, mock_modules
):
    mock_iter_modules.return_value = [
        mock_modules.mod1,
        mock_modules.mod2,
        mock_modules.mod3,
    ]

    mock_load_module.side_effect = lambda name, path: getattr(
        mock_modules, name.rsplit(".", 1)[1]
    )
    mock_pkgs_installed.side_effect = lambda pkgs: pkgs != ["pkg1"]

    graph = build_dependency_graph(mock_modules)

    assert isinstance(graph, nx.DiGraph)
    assert len(graph.nodes) == 3
    assert len(graph.edges) == 2

    mod1 = ModuleComp("regress_stack.modules.mod1", mock_modules.mod1)
    mod2 = ModuleComp("regress_stack.modules.mod2", mock_modules.mod2)
    mod3 = ModuleComp("regress_stack.modules.mod3", mock_modules.mod3)

    assert graph.has_node(mod1)
    assert graph.has_node(mod2)
    assert graph.has_node(mod3)

    assert graph.nodes[mod1]["installed"] is False
    assert graph.nodes[mod2]["installed"] is True
    assert graph.nodes[mod3]["installed"] is True


def test_filter_graph_all_installed(mock_modules):
    nodes = {
        "mysql": {"installed": True},
        "keystone": {"installed": True},
        "rabbitmq": {"installed": True},
        "glance": {"installed": True},
    }
    edges = [
        ("mysql", "keystone", {"optional": False}),
        ("mysql", "glance", {"optional": False}),
        ("keystone", "glance", {"optional": False}),
    ]
    graph = nx.DiGraph()
    graph.add_nodes_from(nodes.items())
    graph.add_edges_from(edges)
    assert len(graph.nodes) == 4
    assert len(graph.edges) == 3
    filtered_graph = filter_graph(graph)

    assert isinstance(filtered_graph, nx.DiGraph)
    assert len(filtered_graph.nodes) == len(graph.nodes)
    assert len(filtered_graph.edges) == len(graph.edges)


def test_filter_graph_some_uninstalled():
    """Tests the following graph:

    mysql -> keystone
    mysql -> glance
    keystone -> glance
    rabbitmq

    Where mysql is not installed, rabbitmq, keystone and glance are installed.

    Expected result is the following graph:
    rabbitmq
    """
    nodes = {
        "mysql": {"installed": False},
        "keystone": {"installed": True},
        "rabbitmq": {"installed": True},
        "glance": {"installed": True},
    }
    edges = [
        ("mysql", "keystone", {"optional": False}),
        ("mysql", "glance", {"optional": False}),
        ("keystone", "glance", {"optional": False}),
    ]
    graph = nx.DiGraph()
    graph.add_nodes_from(nodes.items())
    graph.add_edges_from(edges)
    assert len(graph.nodes) == 4
    assert len(graph.edges) == 3
    filtered_graph = filter_graph(graph)

    assert isinstance(filtered_graph, nx.DiGraph)
    assert len(filtered_graph.nodes) == 1
    assert len(filtered_graph.edges) == 0
    assert graph.has_node("rabbitmq")


def test_filter_graph_optional_dependency_missing():
    """Test the following graph:

    mysql -> keystone
    mysql -> glance
    keystone -> optional -> glance

    Where keystone is not installed, mysql, rabbitmq, and glance are installed.

    Expected result is the following graph:
    mysql -> glance
    rabbitmq
    """
    nodes = {
        "mysql": {"installed": True},
        "keystone": {"installed": False},
        "rabbitmq": {"installed": True},
        "glance": {"installed": True},
    }
    edges = [
        ("mysql", "keystone", {"optional": False}),
        ("mysql", "glance", {"optional": False}),
        ("keystone", "glance", {"optional": True}),
    ]
    graph = nx.DiGraph()
    graph.add_nodes_from(nodes.items())
    graph.add_edges_from(edges)
    assert len(graph.nodes) == 4
    assert len(graph.edges) == 3
    filtered_graph = filter_graph(graph)

    assert isinstance(filtered_graph, nx.DiGraph)
    assert len(filtered_graph.nodes) == 3
    assert len(filtered_graph.edges) == 1
    assert graph.has_node("rabbitmq")
    assert graph.has_node("mysql")
    assert graph.has_node("glance")
    assert graph.has_edge("mysql", "glance")


def test_get_execution_order_without_filtering():
    """Test that get_execution_order with filter_missing=False includes all modules."""
    from regress_stack.core.modules import get_execution_order
    import regress_stack.modules

    # Test with nova target without filtering
    execution_order = get_execution_order(
        regress_stack.modules, "nova", filter_missing=False
    )
    packages = []
    for module_comp in execution_order:
        packages.extend(getattr(module_comp.module, "PACKAGES", []))

    # Should include nova packages
    assert "nova-api" in packages
    assert "nova-conductor" in packages
    # Should include dependency packages even if not installed
    assert "mysql-server" in packages
    assert "keystone" in packages
    assert "rabbitmq-server" in packages


def test_get_execution_order_utils_without_filtering():
    """Test that get_execution_order with utils target returns only utils packages."""
    from regress_stack.core.modules import get_execution_order
    import regress_stack.modules

    execution_order = get_execution_order(
        regress_stack.modules, "utils", filter_missing=False
    )
    packages = []
    for module_comp in execution_order:
        packages.extend(getattr(module_comp.module, "PACKAGES", []))

    assert packages == ["crudini"]


def test_get_execution_order_all_without_filtering():
    """Test that get_execution_order without target returns all packages."""
    from regress_stack.core.modules import get_execution_order
    import regress_stack.modules

    execution_order = get_execution_order(
        regress_stack.modules, None, filter_missing=False
    )
    packages = []
    for module_comp in execution_order:
        packages.extend(getattr(module_comp.module, "PACKAGES", []))

    # Should include packages from all modules
    assert "nova-api" in packages
    assert "keystone" in packages
    assert "heat-api" in packages
    assert "magnum-api" in packages
    assert "crudini" in packages


def test_get_execution_order_invalid_target_without_filtering():
    """Test that get_execution_order raises error for invalid target."""
    from regress_stack.core.modules import get_execution_order
    import regress_stack.modules

    with pytest.raises(RuntimeError, match="Target 'invalid' not found"):
        get_execution_order(regress_stack.modules, "invalid", filter_missing=False)
