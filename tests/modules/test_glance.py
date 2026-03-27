# Copyright 2025 - Canonical Ltd
# SPDX-License-Identifier: GPL-3.0-only

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
