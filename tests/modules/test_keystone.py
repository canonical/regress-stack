# Copyright 2025 - Canonical Ltd
# SPDX-License-Identifier: GPL-3.0-only

from regress_stack.modules import keystone


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
