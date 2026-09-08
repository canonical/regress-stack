# Copyright 2026 - Canonical Ltd
# SPDX-License-Identifier: GPL-3.0-only

import dataclasses
import json
import os
import uuid

import pytest

from regress_stack.core.deployment import (
    Context,
    Deployment,
    Node,
    ProviderNetwork,
    Secret,
    activate,
    current,
    private_read,
    private_write,
)


@pytest.fixture
def inventory():
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


def test_inventory_roundtrip_and_roles(inventory):
    assert (
        Deployment.from_dict(json.loads(json.dumps(dataclasses.asdict(inventory))))
        == inventory
    )
    assert inventory.role("node2") == "controller"
    assert inventory.role("compute1") == "compute"
    with pytest.raises(ValueError):
        inventory.node("unknown")


@pytest.mark.parametrize("count", [0, 1, 2, 4])
def test_hyperconverged_requires_three(inventory, count):
    nodes = tuple(
        Node(f"node{i}", f"192.0.2.{i}", "ens3", "ens4") for i in range(1, count + 1)
    )
    with pytest.raises(ValueError, match="exactly one or three"):
        dataclasses.replace(inventory, controllers=nodes)


def test_single_controller_with_compute(inventory):
    deployment = dataclasses.replace(
        inventory,
        profile="single",
        controllers=inventory.controllers[:1],
        api_address="192.0.2.1",
    )
    assert len(deployment.nodes) == 2
    with pytest.raises(ValueError):
        dataclasses.replace(deployment, api_address="192.0.2.10")


@pytest.mark.parametrize(
    "changes",
    [
        {"profile": "custom"},
        {"api_address": "192.0.2.1"},
        {"api_address": "203.0.113.4"},
        {"management_cidr": "198.51.100.0/24"},
        {"schema": True},
        {"schema": 2},
    ],
)
def test_invalid_inventory(inventory, changes):
    with pytest.raises(ValueError):
        dataclasses.replace(inventory, **changes)


def test_duplicate_identity(inventory):
    with pytest.raises(ValueError, match="names"):
        dataclasses.replace(inventory, computes=(inventory.controllers[0],))
    with pytest.raises(ValueError, match="addresses"):
        dataclasses.replace(
            inventory,
            computes=(dataclasses.replace(inventory.computes[0], address="192.0.2.1"),),
        )


@pytest.mark.parametrize(
    "name", ["../node", "NODE", "node\nother", "-node", "node-", ""]
)
def test_unsafe_node_names(name):
    with pytest.raises(ValueError):
        Node(name, "192.0.2.1", "ens3", "ens4")


def test_invalid_provider_range(inventory):
    with pytest.raises(ValueError):
        dataclasses.replace(inventory.provider, allocation_start="198.51.100.1")
    with pytest.raises(ValueError):
        dataclasses.replace(inventory.provider, gateway="203.0.113.1")


def test_export_scopes_secrets_to_recipient(inventory, tmp_path):
    ctx = Context(inventory, "node1", str(uuid.uuid4()))
    output = tmp_path / "preseeds"
    ctx.export(
        output,
        {
            "mysql/recovery": Secret(
                "controller-secret", frozenset(n.name for n in inventory.controllers)
            ),
            "nova/password": Secret(
                "compute-secret", frozenset(n.name for n in inventory.nodes)
            ),
        },
    )
    compute = Context.read(output / "compute1.json")
    assert compute.values == {"nova/password": "compute-secret"}
    assert (
        Context.read(output / "node2.json").secret("mysql/recovery")
        == "controller-secret"
    )
    assert not (output / "node1.json").exists()
    assert output.stat().st_mode & 0o777 == 0o700
    assert (output / "compute1.json").stat().st_mode & 0o777 == 0o600
    assert "compute-secret" not in repr(compute)
    assert "secret-value" not in repr(Secret("secret-value", frozenset()))


def test_unknown_secret_recipient_rejected(inventory, tmp_path):
    ctx = Context(inventory, "node1", str(uuid.uuid4()))
    with pytest.raises(ValueError):
        ctx.export(
            tmp_path / "seeds", {"secret": Secret("value", frozenset({"unknown"}))}
        )


def test_nonbootstrap_cannot_export(inventory, tmp_path):
    with pytest.raises(ValueError):
        Context(inventory, "node2", str(uuid.uuid4())).export(tmp_path, {})


def test_export_rejects_public_directory(inventory, tmp_path):
    tmp_path.chmod(0o755)
    with pytest.raises(ValueError):
        Context(inventory, "node1", str(uuid.uuid4())).export(tmp_path, {})


def test_private_io_rejects_symlink_and_public_file(tmp_path):
    target = tmp_path / "target"
    private_write(target, "secret")
    assert private_read(target) == "secret"
    link = tmp_path / "link"
    link.symlink_to(target)
    with pytest.raises(OSError):
        private_read(link)
    target.chmod(0o644)
    with pytest.raises(ValueError):
        private_read(target)


def test_write_replaces_symlink_without_touching_target(tmp_path):
    target = tmp_path / "target"
    target.write_text("original")
    link = tmp_path / "link"
    link.symlink_to(target)
    private_write(link, "new")
    assert target.read_text() == "original"
    assert private_read(link) == "new"


def test_context_is_restored_after_failure(inventory):
    ctx = Context(inventory, "node1", str(uuid.uuid4()))
    assert current() is None
    with pytest.raises(RuntimeError):
        with activate(ctx):
            assert current() is ctx
            raise RuntimeError
    assert current() is None


def test_private_io_rejects_fifo(tmp_path):
    fifo = tmp_path / "fifo"
    os.mkfifo(fifo, 0o600)
    with pytest.raises(ValueError):
        private_read(fifo)


def test_node_rejects_numeric_ip():
    with pytest.raises(ValueError, match="must be strings"):
        Node("node1", 3221225985, "ens3", "ens4")
