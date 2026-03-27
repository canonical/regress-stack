# Copyright 2025 - Canonical Ltd
# SPDX-License-Identifier: GPL-3.0-only

import subprocess

from regress_stack.modules import cinder


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
