# Copyright 2025 - Canonical Ltd
# SPDX-License-Identifier: GPL-3.0-only

import subprocess
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from regress_stack.modules import nova


def test_nova_packages_include_qemu_spice_support():
    assert "qemu-system-modules-spice" in nova.determine_packages()


@pytest.mark.parametrize(
    ("version", "spice_package"),
    [
        ("1:6.2+dfsg-2ubuntu6.31", "qemu-system-common"),
        ("1:7.2+dfsg-7ubuntu1", "qemu-system-common"),
        ("1:8.1.0+ds-2", "qemu-system-modules-spice"),
        ("1:8.2.2+ds-0ubuntu1", "qemu-system-modules-spice"),
        ("1:10.2.1+ds-1ubuntu3", "qemu-system-modules-spice"),
    ],
)
def test_nova_selects_spice_package_by_qemu_candidate(
    monkeypatch, version, spice_package
):
    cache = Mock()
    cache.__getitem__ = Mock(
        return_value=SimpleNamespace(
            candidate=SimpleNamespace(version=version),
            installed=SimpleNamespace(version="1:6.2+dfsg-2ubuntu6.31"),
        )
    )
    monkeypatch.setattr(nova.core_apt.apt, "Cache", lambda: cache)
    packages = nova.determine_packages()
    assert packages == [*nova.BASE_PACKAGES, spice_package]


def test_using_sudo_rs(monkeypatch):
    monkeypatch.setattr(
        nova.subprocess,
        "run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess(
            args=["sudo", "-V"], returncode=0, stdout="", stderr="sudo-rs 0.2.8"
        ),
    )
    assert nova._using_sudo_rs() is True


def test_ensure_sudo_rs_rootwrap(tmp_path, monkeypatch):
    sudoers = tmp_path / "nova-rootwrap"
    rootwrap = tmp_path / "nova-rootwrap-bin"
    rootwrap.write_text("")
    run_calls = []
    warnings = []

    monkeypatch.setattr(nova, "NOVA_SUDOERS", sudoers)
    monkeypatch.setattr(nova, "NOVA_ROOTWRAP", rootwrap)
    monkeypatch.setattr(
        nova.core_utils,
        "run",
        lambda cmd, args=(), **_kwargs: run_calls.append((cmd, list(args))) or "",
    )
    monkeypatch.setattr(
        nova.core_utils,
        "warn_workaround",
        lambda subject, detail: warnings.append((subject, detail)),
    )

    nova._ensure_sudo_rs_rootwrap()

    assert sudoers.read_text() == "nova ALL = (root) NOPASSWD: /usr/bin/nova-rootwrap\n"
    assert sudoers.stat().st_mode & 0o777 == 0o440
    assert ("visudo", ["-cf", str(sudoers)]) in run_calls
    assert warnings


def test_ensure_metadata_site_when_missing(tmp_path, monkeypatch):
    sites_enabled = tmp_path / "sites-enabled"
    sites_available = tmp_path / "sites-available"
    sites_enabled.mkdir()
    site_path = sites_available / "regress-stack-nova-metadata.conf"
    run_calls = []
    warnings = []

    monkeypatch.setattr(nova, "APACHE_SITES_ENABLED", sites_enabled)
    monkeypatch.setattr(nova, "NOVA_METADATA_SITE", site_path)
    monkeypatch.setattr(nova, "NOVA_METADATA_SITE_NAME", site_path.name)
    monkeypatch.setattr(
        nova.core_utils,
        "run",
        lambda cmd, args=(), **_kwargs: run_calls.append((cmd, list(args))) or "",
    )
    monkeypatch.setattr(
        nova.core_utils,
        "warn_workaround",
        lambda subject, detail: warnings.append((subject, detail)),
    )

    nova._ensure_metadata_site()

    assert site_path.exists()
    assert (
        "/usr/lib/python3/dist-packages/nova/wsgi/metadata.py" in site_path.read_text()
    )
    assert "WSGIDaemonProcess nova-metadata" in site_path.read_text()
    assert ("a2ensite", [site_path.name]) in run_calls
    assert warnings


def test_ensure_metadata_site_is_noop_when_valid_site_exists(tmp_path, monkeypatch):
    sites_enabled = tmp_path / "sites-enabled"
    sites_available = tmp_path / "sites-available"
    sites_enabled.mkdir()
    sites_available.mkdir()
    site_path = sites_available / "regress-stack-nova-metadata.conf"
    (sites_enabled / "nova-metadata.conf").write_text(
        "Listen 8775\n"
        "WSGIScriptAlias / /usr/lib/python3/dist-packages/nova/wsgi/metadata.py\n"
        "WSGIDaemonProcess nova-metadata processes=1 threads=1 user=nova group=nova display-name=%{GROUP}\n"
        "WSGIProcessGroup nova-metadata\n"
    )
    run_calls = []

    monkeypatch.setattr(nova, "APACHE_SITES_ENABLED", sites_enabled)
    monkeypatch.setattr(nova, "NOVA_METADATA_SITE", site_path)
    monkeypatch.setattr(nova, "NOVA_METADATA_SITE_NAME", site_path.name)
    monkeypatch.setattr(
        nova.core_utils,
        "run",
        lambda cmd, args=(), **_kwargs: run_calls.append((cmd, list(args))) or "",
    )

    nova._ensure_metadata_site()

    assert run_calls == []


def test_ensure_metadata_site_rewrites_stale_managed_site(tmp_path, monkeypatch):
    sites_enabled = tmp_path / "sites-enabled"
    sites_available = tmp_path / "sites-available"
    sites_enabled.mkdir()
    sites_available.mkdir()
    site_path = sites_available / "regress-stack-nova-metadata.conf"
    site_path.write_text(
        "Listen 8775\n"
        "WSGIScriptAlias / /usr/lib/python3/dist-packages/nova/wsgi/metadata.py\n"
        "WSGIDaemonProcess nova-api processes=5 threads=1 user=nova group=nova display-name=%{GROUP}\n"
        "WSGIProcessGroup nova-api\n"
    )
    (sites_enabled / site_path.name).write_text(site_path.read_text())
    run_calls = []

    monkeypatch.setattr(nova, "APACHE_SITES_ENABLED", sites_enabled)
    monkeypatch.setattr(nova, "NOVA_METADATA_SITE", site_path)
    monkeypatch.setattr(nova, "NOVA_METADATA_SITE_NAME", site_path.name)
    monkeypatch.setattr(
        nova.core_utils,
        "run",
        lambda cmd, args=(), **_kwargs: run_calls.append((cmd, list(args))) or "",
    )

    nova._ensure_metadata_site()

    assert "WSGIDaemonProcess nova-metadata" in site_path.read_text()
    assert run_calls == []
