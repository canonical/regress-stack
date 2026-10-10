# Copyright 2026 - Canonical Ltd
# SPDX-License-Identifier: GPL-3.0-only

import pytest


@pytest.mark.parametrize("controllers", [1, 3])
def test_explicit_metadata_agent_routes_through_api_address(monkeypatch, controllers):
    from regress_stack.core.deployment import (
        Context,
        Deployment,
        Node,
        ProviderNetwork,
        activate,
    )
    from regress_stack.modules import neutron

    deployment = Deployment(
        "control",
        tuple(
            Node(f"node{i}", f"192.0.2.{i}", "ens3", "ens4")
            for i in range(1, controllers + 1)
        ),
        (Node("compute1", "192.0.2.4", "ens3", "ens4"),),
        "192.0.2.10" if controllers == 3 else "192.0.2.1",
        "192.0.2.0/24",
        ProviderNetwork(
            "198.51.100.0/24", "198.51.100.1", "198.51.100.20", "198.51.100.80"
        ),
    )
    context = Context(
        deployment,
        "compute1",
        "00000000-0000-0000-0000-000000000001",
        {"neutron/metadata": "secret"},
    )
    options = []
    commands = []
    monkeypatch.setattr(
        neutron.module_utils, "cfg_set", lambda path, *args: options.extend(args)
    )
    monkeypatch.setattr(
        neutron.core_quiet,
        "run",
        lambda command, args: commands.append((command, args)),
    )
    with activate(context):
        neutron.setup_metadata_agent()
    assert ("DEFAULT", "nova_metadata_host", deployment.api_address) in options
    assert (
        "DEFAULT",
        "nova_metadata_port",
        "18775" if controllers == 3 else "8775",
    ) in options
    assert ("DEFAULT", "metadata_proxy_shared_secret", "secret") in options
    assert ("ovs", "ovsdb_connection", "unix:/var/run/openvswitch/db.sock") in options
    assert (
        "ovn",
        "ovn_sb_connection",
        ",".join(f"tcp:{node.address}:6642" for node in deployment.controllers),
    ) in options
    assert commands == [
        ("systemctl", ["enable", "neutron-ovn-metadata-agent"]),
        ("systemctl", ["restart", "neutron-ovn-metadata-agent"]),
    ]
