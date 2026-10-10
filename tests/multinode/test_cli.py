# Copyright 2026 - Canonical Ltd
# SPDX-License-Identifier: GPL-3.0-only

import dataclasses
import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import yaml

from click.testing import CliRunner

from regress_stack.cli.main import main
from regress_stack.core.deployment import activate
from regress_stack.multinode import setup
from regress_stack import topology


def test_compute_package_cli(context, tmp_path):
    inventory = tmp_path / "inventory.json"
    inventory.write_text(json.dumps(context.deployment.to_dict()))
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


def test_yaml_control_inventory_package_cli(context, tmp_path):
    inventory = tmp_path / "inventory.yaml"
    data = context.deployment.to_dict()
    data["profile"] = "control"
    inventory.write_text(yaml.safe_dump(data))
    result = CliRunner().invoke(
        main, ["packages", "--inventory", str(inventory), "--node", "compute1"]
    )
    assert result.exit_code == 0, result.output
    assert "nova-compute" in result.output.split()


def test_setup_rejects_target_with_inventory_before_configuration(
    context, tmp_path, monkeypatch
):
    inventory = tmp_path / "inventory.json"
    inventory.write_text(json.dumps(context.deployment.to_dict()))
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


def test_tempest_refuses_implicit_explicit_state(tmp_path, monkeypatch):
    from regress_stack.cli import test as test_cli

    state = tmp_path / "context.json"
    state.write_text("{}")
    monkeypatch.setattr(test_cli, "LOCAL_STATE", state)
    run = Mock(side_effect=AssertionError("must not run Tempest"))
    monkeypatch.setattr(test_cli, "run_tempest", run)
    result = CliRunner().invoke(main, ["test"])
    assert result.exit_code != 0
    assert "--state" in result.output
    run.assert_not_called()


def test_tempest_legacy_mode_collects_logs(tmp_path, monkeypatch):
    from regress_stack.cli import test as test_cli
    from regress_stack.core.deployment import current

    monkeypatch.setattr(test_cli, "LOCAL_STATE", tmp_path / "missing.json")
    seen = []
    monkeypatch.setattr(
        test_cli,
        "run_tempest",
        lambda *args, **kwargs: seen.append((current(), kwargs)),
    )
    result = CliRunner().invoke(main, ["test"])
    assert result.exit_code == 0, result.output
    assert seen == [(None, {"collect_failure_logs": True})]


@pytest.mark.parametrize("local_name", ["node1", "compute1"])
def test_tempest_explicit_state_runs_on_controllers_only(
    context, tmp_path, monkeypatch, local_name
):
    from regress_stack.cli import test as test_cli
    from regress_stack.core.deployment import current

    local = dataclasses.replace(context, local_name=local_name)
    state = tmp_path / "context.json"
    local.write(state)
    seen = []
    monkeypatch.setattr(
        test_cli,
        "run_tempest",
        lambda *args, **kwargs: seen.append((current(), kwargs)),
    )
    result = CliRunner().invoke(main, ["test", "--state", str(state)])
    if local_name == "compute1":
        assert result.exit_code != 0
        assert "controller" in result.output
        assert seen == []
        return
    assert result.exit_code == 0, result.output
    [(active, options)] = seen
    assert active.local_name == "node1"
    assert options == {"collect_failure_logs": False}


def test_runtime_resources_can_be_created_on_surviving_controller(peer):
    with activate(peer):
        assert not topology.owns_shared_state()
    with activate(peer, bootstrap_only=False):
        assert topology.owns_shared_state()
    with activate(peer):
        assert not topology.owns_shared_state()


def test_quiet_context_mutes_only_third_party_http_logging(context):
    import logging

    root = logging.getLogger()
    saved = root.level
    root.setLevel(logging.DEBUG)
    try:
        with setup.quiet_context(context):
            assert root.level == logging.DEBUG
            for name in ("openstack", "keystoneauth", "urllib3"):
                assert logging.getLogger(name).level == logging.WARNING
        for name in ("openstack", "keystoneauth", "urllib3"):
            assert logging.getLogger(name).level == logging.NOTSET
    finally:
        root.setLevel(saved)


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
    monkeypatch.setattr(setup.profiles, "verify_installed", packages)
    import pytest

    with pytest.raises(RuntimeError, match="Management address"):
        setup.local_preflight(context)
    packages.assert_not_called()


def test_bootstrap_setup_export_is_last(context, tmp_path, monkeypatch):
    inventory = tmp_path / "inventory.json"
    inventory.write_text(json.dumps(context.deployment.to_dict()))
    state = tmp_path / "state"
    monkeypatch.setattr(setup.common, "STATE", state)
    monkeypatch.setattr(setup, "LOCAL_STATE", state / "context.json")
    monkeypatch.setattr(setup, "local_preflight", Mock())
    monkeypatch.setattr(setup.preseed, "generate", lambda _ctx: dict(context.values))
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
    from regress_stack.roles import Role

    monkeypatch.setattr(setup.services, "configure", order.append)
    modules = [
        SimpleNamespace(name="mysql", role=Role(configured_by="recipe")),
        SimpleNamespace(name="nova", role=Role()),
    ]
    monkeypatch.setattr(setup.profiles, "local_modules", lambda _context: modules)
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
    monkeypatch.setattr(setup.profiles, "verify_installed", packages)

    setup.local_preflight(context)

    packages.assert_called_once_with(context)
