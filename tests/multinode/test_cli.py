# Copyright 2026 - Canonical Ltd
# SPDX-License-Identifier: GPL-3.0-only

import dataclasses
import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from click.testing import CliRunner

from regress_stack.cli.main import main
from regress_stack.core.deployment import activate
from regress_stack.multinode import setup
from regress_stack.modules import utils


def test_compute_package_cli(context, tmp_path):
    inventory = tmp_path / "inventory.json"
    inventory.write_text(json.dumps(dataclasses.asdict(context.deployment)))
    result = CliRunner().invoke(
        main, ["packages", "--inventory", str(inventory), "--node", "compute1"]
    )
    assert result.exit_code == 0, result.output
    packages = result.output.split()
    assert "nova-compute" in packages
    assert not set(packages) & {
        "mysql-server",
        "rabbitmq-server",
        "nova-api",
        "keystone",
    }


def test_setup_rejects_target_with_inventory_before_configuration(
    context, tmp_path, monkeypatch
):
    inventory = tmp_path / "inventory.json"
    inventory.write_text(json.dumps(dataclasses.asdict(context.deployment)))
    run = Mock(side_effect=AssertionError("must reject before configuration"))
    monkeypatch.setattr(setup, "run", run)
    result = CliRunner().invoke(main, ["setup", "nova", "--inventory", str(inventory)])
    assert result.exit_code != 0
    run.assert_not_called()


def test_local_setup_message_does_not_claim_readiness(monkeypatch):
    monkeypatch.setattr(setup, "run", Mock())
    result = CliRunner().invoke(main, ["setup", "--node", "node1"])
    assert result.exit_code == 0
    assert "Local setup complete" in result.output
    assert "after all nodes have joined" in result.output


def test_runtime_resources_can_be_created_on_surviving_controller(peer):
    with activate(peer):
        assert not utils.bootstrap()
    with activate(peer, bootstrap_only=False):
        assert utils.bootstrap()
    with activate(peer):
        assert not utils.bootstrap()


def test_preflight_rejects_address_mismatch_before_package_checks(context, monkeypatch):
    monkeypatch.setattr(setup.os, "geteuid", lambda: 0)
    monkeypatch.setattr(setup.utils, "release", lambda: "noble")
    monkeypatch.setattr(setup.socket, "gethostname", lambda: "node1")
    monkeypatch.setattr(
        setup.common,
        "run",
        lambda *args: json.dumps(
            [
                {"ifname": "ens3", "addr_info": [{"local": "192.0.2.99"}]},
                {"ifname": "ens4", "addr_info": []},
            ]
        ),
    )
    packages = Mock(side_effect=AssertionError("must reject identity first"))
    monkeypatch.setattr(setup.profiles, "execution_order", packages)
    import pytest

    with pytest.raises(RuntimeError, match="Management address"):
        setup.local_preflight(context)
    packages.assert_not_called()


def test_bootstrap_setup_export_is_last(context, tmp_path, monkeypatch):
    inventory = tmp_path / "inventory.json"
    inventory.write_text(json.dumps(dataclasses.asdict(context.deployment)))
    state = tmp_path / "state"
    monkeypatch.setattr(setup.common, "STATE", state)
    monkeypatch.setattr(setup, "LOCAL_STATE", state / "context.json")
    monkeypatch.setattr(setup, "local_preflight", Mock())
    monkeypatch.setattr(
        setup.preseed, "generate", lambda ctx: ctx.values.update(context.values)
    )
    order = []
    monkeypatch.setattr(setup.common, "write", Mock())
    monkeypatch.setattr(setup.common, "done", lambda _: False)
    monkeypatch.setattr(setup.common, "mark", lambda name: order.append(name))
    monkeypatch.setattr(setup.utils, "mark_setup", Mock())
    for name in (
        "database",
        "access",
        "messaging",
        "coordination",
        "storage",
        "networking",
    ):
        monkeypatch.setattr(
            getattr(setup, name), "setup", lambda name=name: order.append(name)
        )
    module = SimpleNamespace(
        name="nova", module=SimpleNamespace(setup=lambda: order.append("nova"))
    )
    monkeypatch.setattr(
        setup.profiles, "execution_order", lambda *args, **kwargs: [module]
    )
    monkeypatch.setattr(setup.services, "prepare", Mock())
    monkeypatch.setattr(setup.services, "finish", Mock())
    monkeypatch.setattr(type(context), "export", lambda *args: order.append("export"))
    setup.run(inventory, "node1", export=tmp_path / "exports")
    assert order == [
        "database",
        "access",
        "messaging",
        "coordination",
        "storage",
        "networking",
        "nova",
        "service-nova",
        "export",
        "local-setup",
    ]


@pytest.mark.parametrize(
    "release,nova_version",
    [("jammy", "29.0.0"), ("noble", "30.0.0"), ("resolute", "33.0.0")],
)
def test_preflight_accepts_other_releases(
    context, tmp_path, monkeypatch, release, nova_version
):
    from regress_stack.core import apt

    monkeypatch.setattr(setup.os, "geteuid", lambda: 0)
    monkeypatch.setattr(setup.utils, "release", lambda: release)
    monkeypatch.setattr(apt, "get_upstream_pkg_version", lambda _: nova_version)
    monkeypatch.setattr(setup.socket, "gethostname", lambda: context.local.name)
    monkeypatch.setattr(setup, "LOCAL_STATE", tmp_path / "context.json")
    monkeypatch.setattr(setup.utils, "REGRESS_STACK_DIR", tmp_path)
    monkeypatch.setattr(
        setup.common,
        "run",
        lambda *args: json.dumps(
            [
                {"ifname": "ens3", "addr_info": [{"local": context.local.address}]},
                {"ifname": "ens4", "addr_info": []},
            ]
        ),
    )
    packages = Mock(return_value=[])
    monkeypatch.setattr(setup.profiles, "execution_order", packages)

    setup.local_preflight(context)

    packages.assert_called_once_with(context)
