# Copyright 2026 - Canonical Ltd
# SPDX-License-Identifier: GPL-3.0-only

import json
from unittest.mock import Mock

from regress_stack.multinode import profiles, setup
from regress_stack.roles import Role


def test_setup_dispatches_roles_before_export(context, tmp_path, monkeypatch):
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
    configure = Mock(side_effect=order.append)
    monkeypatch.setattr(setup.services, "configure", configure)
    modules = [
        profiles.LocalModule("mysql", Role(configured_by="recipe")),
        profiles.LocalModule(
            "neutron", Role(setup=lambda: order.append("metadata-agent"))
        ),
        profiles.LocalModule("nova", Role()),
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
        "metadata-agent",
        "service-neutron",
        "nova",
        "service-nova",
        "export",
        "local-setup",
    ]
    configure.assert_called_once_with("nova")
