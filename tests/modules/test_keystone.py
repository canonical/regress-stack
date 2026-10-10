# Copyright 2025 - Canonical Ltd
# SPDX-License-Identifier: GPL-3.0-only

from types import SimpleNamespace

from regress_stack.core import deployment
from regress_stack.modules import keystone


def test_service_account_receives_service_role(monkeypatch):
    assignments = []
    identity = SimpleNamespace(
        assign_project_role_to_user=lambda project, user, role: assignments.append(
            (project, user, role)
        )
    )
    monkeypatch.setattr(deployment, "current", lambda: None)
    monkeypatch.setattr(keystone, "ensure_user", lambda *_args: "user-id")
    monkeypatch.setattr(keystone, "service_domain", lambda: "domain-id")
    monkeypatch.setattr(keystone, "service_project", lambda: "project-id")
    monkeypatch.setattr(keystone, "ensure_admin", lambda *_args: None)
    monkeypatch.setattr(keystone, "ensure_role", lambda name: SimpleNamespace(id=name))
    monkeypatch.setattr(keystone, "o7k", lambda: SimpleNamespace(identity=identity))
    monkeypatch.setattr(keystone, "ensure_service", lambda *_args: "service-id")
    monkeypatch.setattr(keystone, "ensure_endpoint", lambda *_args: None)

    assert keystone.ensure_service_account("nova", "compute", "http://api") == (
        "nova",
        "changeme",
    )
    assert assignments == [("project-id", "user-id", "service")]


def test_ensure_wsgi_scripts(tmp_path, monkeypatch):
    public = tmp_path / "keystone-wsgi-public"
    admin = tmp_path / "keystone-wsgi-admin"
    warnings = []

    monkeypatch.setattr(keystone, "PUBLIC_WSGI", public)
    monkeypatch.setattr(keystone, "ADMIN_WSGI", admin)
    monkeypatch.setattr(
        keystone.core_utils,
        "warn_workaround",
        lambda subject, detail: warnings.append((subject, detail)),
    )

    keystone._ensure_wsgi_scripts()

    assert public.exists()
    assert admin.exists()
    assert public.stat().st_mode & 0o777 == 0o755
    assert admin.stat().st_mode & 0o777 == 0o755
    assert len(warnings) == 2


def test_ensure_wsgi_scripts_is_noop_when_present(tmp_path, monkeypatch):
    public = tmp_path / "keystone-wsgi-public"
    admin = tmp_path / "keystone-wsgi-admin"
    public.write_text("public")
    admin.write_text("admin")
    warnings = []

    monkeypatch.setattr(keystone, "PUBLIC_WSGI", public)
    monkeypatch.setattr(keystone, "ADMIN_WSGI", admin)
    monkeypatch.setattr(
        keystone.core_utils,
        "warn_workaround",
        lambda subject, detail: warnings.append((subject, detail)),
    )

    keystone._ensure_wsgi_scripts()

    assert public.read_text() == "public"
    assert admin.read_text() == "admin"
    assert warnings == []
