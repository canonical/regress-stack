# Copyright 2026 - Canonical Ltd
# SPDX-License-Identifier: GPL-3.0-only

import json
import pathlib

from regress_stack.modules import mistral


def test_wait_for_api_accepts_401(monkeypatch):
    responses = [401]

    monkeypatch.setattr(mistral, "_http_status", lambda _url: responses.pop(0))

    mistral._wait_for_api()


def test_wait_for_api_retries_until_ready(monkeypatch):
    responses = [None, 300]
    sleeps = []

    monkeypatch.setattr(mistral, "_http_status", lambda _url: responses.pop(0))
    monkeypatch.setattr(mistral.time, "sleep", lambda seconds: sleeps.append(seconds))

    mistral._wait_for_api()

    assert sleeps == [5]


def test_setup(monkeypatch):
    cfg_calls = []
    sudo_calls = []
    restarts = []

    monkeypatch.setattr(mistral.mysql, "ensure_service", lambda name: (name, "db-pass"))
    monkeypatch.setattr(
        mistral.rabbitmq, "ensure_service", lambda name: (name, "rabbit-pass")
    )
    monkeypatch.setattr(
        mistral.keystone,
        "ensure_service_account",
        lambda name, service_type, url: (name, "svc-pass"),
    )
    monkeypatch.setattr(
        mistral.mysql,
        "connection_string",
        lambda database,
        username,
        password: f"mysql://{database}:{username}:{password}",
    )
    monkeypatch.setattr(
        mistral.rabbitmq,
        "transport_url",
        lambda username,
        password: f"rabbit://{username}:{password}@localhost/openstack",
    )
    monkeypatch.setattr(
        mistral.keystone,
        "authtoken_service",
        lambda username, password: {"username": username, "password": password},
    )
    monkeypatch.setattr(
        mistral.keystone,
        "account_dict",
        lambda username, password: {"username": username, "password": password},
    )
    monkeypatch.setattr(mistral.core_utils, "my_ip", lambda: "10.0.0.10")
    monkeypatch.setattr(mistral.core_utils, "fqdn", lambda: "regress.local")
    monkeypatch.setattr(
        mistral.module_utils,
        "cfg_set",
        lambda config_file, *args: cfg_calls.append((config_file, args)),
    )
    monkeypatch.setattr(
        mistral.core_utils,
        "sudo",
        lambda cmd, args, user=None: sudo_calls.append((cmd, list(args), user)),
    )
    monkeypatch.setattr(
        mistral.core_utils, "restart_service", lambda service: restarts.append(service)
    )
    monkeypatch.setattr(mistral, "_wait_for_api", lambda: None)

    mistral.setup()

    assert cfg_calls
    assert cfg_calls[0][0] == mistral.CONF
    written = dict(
        (section + "." + key, value) for section, key, value in cfg_calls[0][1]
    )
    assert written["database.connection"] == "mysql://mistral:mistral:db-pass"
    assert (
        written["DEFAULT.transport_url"]
        == "rabbit://mistral:rabbit-pass@localhost/openstack"
    )
    assert written["api.host"] == "10.0.0.10"
    assert written["api.port"] == "8989"
    assert written["service_credentials.username"] == "mistral"
    assert sudo_calls == [("mistral-db-manage", ["upgrade", "head"], "mistral")]
    assert restarts == [
        "mistral-api",
        "mistral-engine",
        "mistral-executor",
        "mistral-event-engine",
    ]


def test_smoke_test(monkeypatch):
    calls = []

    monkeypatch.setattr(mistral, "_wait_for_api", lambda: calls.append("api"))
    monkeypatch.setattr(
        mistral,
        "_openstack",
        lambda args, system_scope=False: calls.append((list(args), system_scope))
        or json.dumps(
            [{"Interface": "public"}, {"Interface": "internal"}, {"Interface": "admin"}]
        ),
    )
    monkeypatch.setattr(
        mistral,
        "_run_smoke_workflow",
        lambda workspace_dir: calls.append(("workflow", workspace_dir)),
    )

    mistral.smoke_test(pathlib.Path("/tmp/workspace"))

    assert calls == [
        "api",
        (["endpoint", "list", "--service", "mistral", "-f", "json"], False),
        ("workflow", pathlib.Path("/tmp/workspace")),
    ]


def test_smoke_test_fails_on_missing_endpoints(monkeypatch):
    monkeypatch.setattr(mistral, "_wait_for_api", lambda: None)
    monkeypatch.setattr(
        mistral,
        "_openstack",
        lambda args, system_scope=False: json.dumps([{"Interface": "public"}]),
    )

    try:
        mistral.smoke_test(None)
    except RuntimeError as exc:
        assert "endpoints are incomplete" in str(exc)
    else:
        raise AssertionError("smoke_test should fail when endpoints are missing")


def test_run_smoke_workflow(monkeypatch, tmp_path):
    commands = []
    workflow_names = []
    states = iter(
        [
            json.dumps({"ID": "exec-1"}),
            json.dumps({"State": "RUNNING"}),
            json.dumps({"State": "SUCCESS"}),
        ]
    )

    def fake_mistral(args):
        commands.append(list(args))
        if args[0] == "workflow-create":
            workflow_names.append(pathlib.Path(args[1]).stem)
        if args[0] == "execution-create":
            return next(states)
        if args[0] == "execution-get":
            return next(states)
        return ""

    monkeypatch.setattr(mistral, "_mistral", fake_mistral)
    monkeypatch.setattr(mistral.time, "sleep", lambda _seconds: None)

    mistral._run_smoke_workflow(tmp_path)

    assert commands[0][0] == "workflow-create"
    assert commands[1] == ["execution-create", workflow_names[0], "{}", "-f", "json"]
    assert commands[-1] == ["workflow-delete", workflow_names[0]]
