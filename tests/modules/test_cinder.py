# Copyright 2025 - Canonical Ltd
# SPDX-License-Identifier: GPL-3.0-only

import subprocess

from regress_stack.modules import cinder


def test_get_service_type_block_storage(monkeypatch):
    monkeypatch.setattr(cinder.core_utils, "run", lambda *_args, **_kwargs: "42.0.0\n")

    assert cinder.get_service_type() == cinder._SERVICE_TYPE


def test_get_service_type_legacy(monkeypatch):
    monkeypatch.setattr(
        cinder.core_utils, "run", lambda *_args, **_kwargs: "tempest 41.9.0\n"
    )

    assert cinder.get_service_type() == cinder._LEGACY_SERVICE_TYPE


def test_get_service_type_legacy_on_tempest_command_failure(monkeypatch):
    def raise_file_not_found(*_args, **_kwargs):
        raise FileNotFoundError

    monkeypatch.setattr(cinder.core_utils, "run", raise_file_not_found)

    assert cinder.get_service_type() == cinder._LEGACY_SERVICE_TYPE


def test_get_service_type_legacy_on_tempest_command_error(monkeypatch):
    def raise_called_process_error(*_args, **_kwargs):
        raise subprocess.CalledProcessError(1, ["tempest", "--version"])

    monkeypatch.setattr(cinder.core_utils, "run", raise_called_process_error)

    assert cinder.get_service_type() == cinder._LEGACY_SERVICE_TYPE


def test_get_service_type_legacy_on_unparseable_version(monkeypatch):
    monkeypatch.setattr(cinder.core_utils, "run", lambda *_args, **_kwargs: "tempest\n")

    assert cinder.get_service_type() == cinder._LEGACY_SERVICE_TYPE


def test_using_sudo_rs(monkeypatch):
    monkeypatch.setattr(
        cinder.subprocess,
        "run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess(
            args=["sudo", "-V"], returncode=0, stdout="", stderr="sudo-rs 0.2.8"
        ),
    )
    assert cinder._using_sudo_rs() is True


def test_ensure_sudo_rs_rootwrap(tmp_path, monkeypatch):
    sudoers = tmp_path / "cinder-rootwrap"
    rootwrap = tmp_path / "cinder-rootwrap-bin"
    rootwrap.write_text("")
    run_calls = []
    warnings = []

    monkeypatch.setattr(cinder, "CINDER_SUDOERS", sudoers)
    monkeypatch.setattr(cinder, "CINDER_ROOTWRAP", rootwrap)
    monkeypatch.setattr(
        cinder.core_utils,
        "run",
        lambda cmd, args=(), **_kwargs: run_calls.append((cmd, list(args))) or "",
    )
    monkeypatch.setattr(
        cinder.core_utils,
        "warn_workaround",
        lambda subject, detail: warnings.append((subject, detail)),
    )

    cinder._ensure_sudo_rs_rootwrap()

    assert (
        sudoers.read_text()
        == "cinder ALL = (root) NOPASSWD: /usr/bin/cinder-rootwrap\n"
    )
    assert sudoers.stat().st_mode & 0o777 == 0o440
    assert ("visudo", ["-cf", str(sudoers)]) in run_calls
    assert warnings


def test_ensure_questing_compat(monkeypatch):
    cfg_calls = []
    compat_calls = []

    monkeypatch.setattr(cinder, "_using_sudo_rs", lambda: True)
    monkeypatch.setattr(
        cinder, "_ensure_sudo_rs_rootwrap", lambda: compat_calls.append("sudoers")
    )
    monkeypatch.setattr(
        cinder.module_utils,
        "cfg_set",
        lambda config, *args: cfg_calls.append((config, args)),
    )

    cinder._ensure_questing_compat()

    assert compat_calls == ["sudoers"]
    assert cfg_calls == [
        (
            cinder.CONF,
            (
                (
                    "cinder_sys_admin",
                    "helper_command",
                    cinder.CINDER_PRIVSEP_HELPER,
                ),
            ),
        )
    ]


def test_tempest_block_storage_discovery(monkeypatch, tmp_path):
    from types import SimpleNamespace
    from unittest.mock import Mock

    monkeypatch.setattr(cinder, "get_service_type", lambda: "block-storage")
    proxy = Mock()
    proxy.get_endpoint_data.return_value = SimpleNamespace(
        min_microversion=(3, 0), max_microversion=(3, 71)
    )
    services, pools = Mock(), Mock()
    services.json.return_value = {"services": []}
    pools.json.return_value = {"pools": [{"name": "regress-stack@ceph#ceph"}]}
    proxy.get.side_effect = [services, pools]
    monkeypatch.setattr(
        cinder.keystone, "o7k", lambda: SimpleNamespace(block_storage=proxy)
    )
    configure = Mock()
    monkeypatch.setattr(cinder.module_utils, "cfg_set", configure)
    cinder.configure_tempest(tmp_path / "tempest.conf")
    options = configure.call_args.args[1:]
    assert ("service_available", "cinder", "True") in options
    assert ("volume", "catalog_type", "block-storage") in options
    assert ("volume", "max_microversion", "3.71") in options
    assert ("volume-feature-enabled", "backup", "False") in options
    services.raise_for_status.assert_called_once_with()
    pools.raise_for_status.assert_called_once_with()


def test_tempest_legacy_cinder_discovery_is_preserved(monkeypatch, tmp_path):
    from unittest.mock import Mock

    monkeypatch.setattr(cinder, "get_service_type", lambda: "volumev3")
    configure = Mock()
    monkeypatch.setattr(cinder.module_utils, "cfg_set", configure)
    cinder.configure_tempest(tmp_path / "tempest.conf")
    configure.assert_not_called()
