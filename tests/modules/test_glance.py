# Copyright 2025 - Canonical Ltd
# SPDX-License-Identifier: GPL-3.0-only

import pytest

from regress_stack.modules import glance


def test_disable_strict_image_format_validation(monkeypatch):
    cfg_calls = []
    warnings = []

    class _Version:
        def __ge__(self, other):
            return other == glance.GLANCE_STRICT_IMAGE_FORMAT_VERSION

        def __lt__(self, _other):
            return False

    monkeypatch.setattr(
        glance.core_apt, "PkgVersionCompare", lambda *args, **kwargs: _Version()
    )
    monkeypatch.setattr(
        glance.module_utils,
        "cfg_set",
        lambda config, *args: cfg_calls.append((config, args)),
    )
    monkeypatch.setattr(
        glance.core_utils,
        "warn_workaround",
        lambda subject, detail: warnings.append((subject, detail)),
    )

    glance._disable_strict_image_format_validation()

    assert warnings
    assert cfg_calls == [
        (
            glance.CONF,
            (("image_format", "require_image_format_match", "false"),),
        )
    ]


def test_disable_strict_image_format_validation_noop(monkeypatch):
    cfg_calls = []
    warnings = []

    class _Version:
        def __lt__(self, other):
            return other == glance.GLANCE_STRICT_IMAGE_FORMAT_VERSION

    monkeypatch.setattr(
        glance.core_apt, "PkgVersionCompare", lambda *args, **kwargs: _Version()
    )
    monkeypatch.setattr(
        glance.module_utils,
        "cfg_set",
        lambda config, *args: cfg_calls.append((config, args)),
    )
    monkeypatch.setattr(
        glance.core_utils,
        "warn_workaround",
        lambda subject, detail: warnings.append((subject, detail)),
    )

    glance._disable_strict_image_format_validation()

    assert warnings == []
    assert cfg_calls == []


def _patch_setup(monkeypatch, events):
    monkeypatch.setattr(glance.mysql, "ensure_service", lambda _s: ("db-u", "db-p"))
    monkeypatch.setattr(
        glance.mysql, "connection_string", lambda *_a: "mysql+pymysql://glance"
    )
    monkeypatch.setattr(
        glance.keystone, "ensure_service_account", lambda *_a: ("u", "p")
    )
    monkeypatch.setattr(glance.keystone, "authtoken_service", lambda *_a: {})
    monkeypatch.setattr(glance.module_utils, "dict_to_cfg_set_args", lambda *_a: ())
    monkeypatch.setattr(glance.module_utils, "cfg_set", lambda *_a, **_k: None)
    monkeypatch.setattr(glance, "_ensure_staging_dir", lambda: events.append("staging"))
    monkeypatch.setattr(
        glance,
        "_disable_strict_image_format_validation",
        lambda: events.append("format"),
    )
    monkeypatch.setattr(glance.barbican, "installed", lambda: False)
    monkeypatch.setattr(
        glance.core_utils,
        "sudo",
        lambda c, a, user=None: events.append(("sudo", c, a, user)),
    )
    monkeypatch.setattr(
        glance.core_utils,
        "restart_service",
        lambda svc: events.append(("restart", svc)),
    )
    monkeypatch.setattr(
        glance.core_utils, "graceful_apache", lambda: events.append("reload")
    )


@pytest.mark.parametrize("unit_exists", [True, False])
def test_setup_restarts_or_reloads_glance_api(monkeypatch, unit_exists):
    events = []
    _patch_setup(monkeypatch, events)
    monkeypatch.setattr(glance.core_utils, "has_service", lambda _svc: unit_exists)

    glance.setup()

    assert "staging" in events
    assert "format" in events
    assert ("sudo", "glance-manage", ["db_sync"], "glance") in events
    if unit_exists:
        assert ("restart", "glance-api") in events
        assert "reload" not in events
    else:
        assert "reload" in events
        assert ("restart", "glance-api") not in events
