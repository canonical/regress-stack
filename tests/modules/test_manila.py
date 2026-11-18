# Copyright 2026 - Canonical Ltd
# SPDX-License-Identifier: GPL-3.0-only

from unittest.mock import Mock, call

import pytest

from regress_stack.modules import manila


def test_existing_volume_group_is_activated(monkeypatch):
    """Re-running setup activates existing LVs without attaching another loop device."""
    monkeypatch.setattr(manila, "_volume_group_exists", lambda: True)
    run = Mock()
    monkeypatch.setattr(manila.core_utils, "run", run)

    manila._ensure_volume_group()

    run.assert_called_once_with("vgchange", ["-ay", manila.VOLUME_GROUP])


def test_rebooted_volume_group_reuses_backing_file(tmp_path, monkeypatch):
    """Reattach persistent storage after reboot without recreating its VG or data."""
    backing = tmp_path / "volumes.img"
    backing.write_bytes(b"existing data")
    monkeypatch.setattr(manila, "BACKING_FILE", backing)
    monkeypatch.setattr(manila, "_volume_group_exists", Mock(side_effect=[False, True]))
    run = Mock(side_effect=["", "/dev/loop7", ""])
    monkeypatch.setattr(manila.core_utils, "run", run)

    manila._ensure_volume_group()

    assert backing.read_bytes() == b"existing data"
    assert run.call_args_list == [
        call(
            "losetup",
            ["--list", "--noheadings", "--output", "NAME", "-j", str(backing)],
        ),
        call("losetup", ["--find", "--show", "--nooverlap", str(backing)]),
        call("vgchange", ["-ay", manila.VOLUME_GROUP]),
    ]


@pytest.mark.parametrize("has_unit", [False, True])
def test_wsgi_api_handles_optional_systemd_unit(tmp_path, monkeypatch, has_unit):
    """WSGI packages need no API unit and reload Apache without dropping connections."""
    script = tmp_path / "manila-api-wsgi"
    script.touch()
    config = tmp_path / "manila.conf"
    monkeypatch.setattr(manila, "API_WSGI", script)
    monkeypatch.setattr(manila, "APACHE_CONF", config)
    monkeypatch.setattr(manila.core_utils, "has_service", lambda _: has_unit)
    run, reload = Mock(), Mock()
    monkeypatch.setattr(manila.core_utils, "run", run)
    monkeypatch.setattr(manila.core_utils, "graceful_apache", reload)

    manila._start_api()

    assert f"WSGIScriptAlias / {script}" in config.read_text()
    expected = [call("a2enconf", [config.stem])]
    if has_unit:
        expected.insert(0, call("systemctl", ["disable", "--now", "manila-api"]))
    assert run.call_args_list == expected
    reload.assert_called_once_with()


def test_legacy_api_uses_systemd(tmp_path, monkeypatch):
    """Older packages without a WSGI entry point retain the standalone API service."""
    monkeypatch.setattr(manila, "API_WSGI", tmp_path / "missing")
    restart = Mock()
    monkeypatch.setattr(manila.core_utils, "restart_service", restart)

    manila._start_api()

    restart.assert_called_once_with("manila-api")
