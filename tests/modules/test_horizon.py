# Copyright 2026 - Canonical Ltd
# SPDX-License-Identifier: GPL-3.0-only

from regress_stack.modules import horizon


def test_upsert_setting_replaces_existing_value():
    content = "OPENSTACK_HOST = 'old.example'\nOTHER = 1\n"

    updated = horizon._upsert_setting(content, "OPENSTACK_HOST", "'10.0.0.1'")

    assert updated == "OPENSTACK_HOST = '10.0.0.1'\nOTHER = 1\n"


def test_upsert_setting_appends_when_missing():
    content = "OTHER = 1\n"

    updated = horizon._upsert_setting(content, "OPENSTACK_HOST", "'10.0.0.1'")

    assert updated.endswith("OPENSTACK_HOST = '10.0.0.1'\n")


def test_setup_updates_local_settings_and_restarts_apache(tmp_path, monkeypatch):
    settings_path = tmp_path / "local_settings.py"
    settings_path.write_text(
        "OPENSTACK_HOST = 'controller'\nALLOWED_HOSTS = ['*']\nOTHER = 1\n"
    )

    restart_calls = []

    monkeypatch.setattr(horizon, "LOCAL_SETTINGS", settings_path)
    monkeypatch.setattr(horizon.core_utils, "my_ip", lambda: "10.0.0.10")
    monkeypatch.setattr(horizon.core_utils, "fqdn", lambda: "node1.example")
    monkeypatch.setattr(
        horizon.core_utils,
        "restart_apache",
        lambda: restart_calls.append("apache2"),
    )

    horizon.setup()

    result = settings_path.read_text()
    assert "OPENSTACK_HOST = '10.0.0.10'" in result
    assert (
        "ALLOWED_HOSTS = ['localhost', '127.0.0.1', '10.0.0.10', 'node1.example']"
        in result
    )
    assert restart_calls == ["apache2"]


def test_setup_fails_when_settings_file_missing(tmp_path, monkeypatch):
    settings_path = tmp_path / "missing_local_settings.py"

    monkeypatch.setattr(horizon, "LOCAL_SETTINGS", settings_path)

    try:
        horizon.setup()
    except RuntimeError as exc:
        assert "Expected Horizon settings file missing" in str(exc)
    else:
        assert False, "expected RuntimeError"
