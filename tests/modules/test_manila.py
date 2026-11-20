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


@pytest.mark.parametrize("mode", ["cluster", "standalone"])
def test_cephfs_mode_selects_export_manager(tmp_path, monkeypatch, mode):
    """Select the requested helper without preparing the other Ganesha backend."""
    monkeypatch.setattr(manila, "CEPHFS_NFS_MODE", mode)
    monkeypatch.setattr(manila, "_check_cluster_support", Mock())
    monkeypatch.setattr(manila.pathlib, "Path", lambda _: tmp_path / "manila.keyring")
    monkeypatch.setattr(manila.shutil, "chown", Mock())
    monkeypatch.setattr(manila.ceph, "ensure_osd", Mock())
    monkeypatch.setattr(manila.ceph, "ensure_filesystem", Mock())
    cluster = Mock()
    standalone = Mock(return_value={"ganesha_rados_store_enable": "True"})
    monkeypatch.setattr(manila.ceph, "ensure_nfs_cluster", cluster)
    monkeypatch.setattr(manila, "_configure_standalone_ganesha", standalone)
    monkeypatch.setattr(manila.core_utils, "has_service", lambda _: False)
    (tmp_path / "manila.keyring").touch()
    monkeypatch.setattr(manila.core_utils, "run", Mock())

    settings = manila._configure_cephfs_nfs()

    assert settings["cephfs_protocol_helper_type"] == "NFS"
    if mode == "cluster":
        cluster.assert_called_once_with(manila.FILESYSTEM)
        standalone.assert_not_called()
        assert settings["cephfs_nfs_cluster_id"] == manila.FILESYSTEM
        assert "ganesha_rados_store_enable" not in settings
    else:
        standalone.assert_called_once_with()
        cluster.assert_not_called()
        assert "cephfs_nfs_cluster_id" not in settings


def test_invalid_cephfs_mode_fails_before_setup(monkeypatch):
    """Reject misspelled modes rather than silently using standalone Ganesha."""
    monkeypatch.setattr(manila, "BACKEND", "cephfs-nfs")
    monkeypatch.setattr(manila, "CEPHFS_NFS", True)
    monkeypatch.setattr(manila, "CEPHFS_NFS_MODE", "typo")
    configure = Mock()
    monkeypatch.setattr(manila, "_configure_cephfs_nfs", configure)

    with pytest.raises(ValueError, match="MANILA_CEPHFS_NFS_MODE"):
        manila.setup()

    configure.assert_not_called()


@pytest.mark.parametrize(
    "backend,mode,expected,excluded",
    [
        ("lvm", "cluster", "nfs-kernel-server", "cephadm"),
        ("cephfs-nfs", "cluster", "cephadm", "nfs-ganesha"),
        ("cephfs-nfs", "standalone", "nfs-ganesha-ceph", "cephadm"),
    ],
)
def test_backend_environment_selects_packages(backend, mode, expected, excluded):
    """Each fresh CLI process resolves runtime packages for its selected NFS mode."""
    import json
    import os
    import subprocess
    import sys

    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import json; from regress_stack.modules import manila; "
            "print(json.dumps(manila.PACKAGES))",
        ],
        env={**os.environ, "MANILA_BACKEND": backend, "MANILA_CEPHFS_NFS_MODE": mode},
        text=True,
        capture_output=True,
        check=True,
    )
    packages = json.loads(result.stdout)
    assert expected in packages
    assert excluded not in packages


@pytest.mark.parametrize("supports_cluster", [False, True])
def test_cluster_support_checked_before_provisioning(
    tmp_path, monkeypatch, supports_cluster
):
    """Yoga cannot select a helper it does not contain; newer packages are accepted."""
    package = tmp_path / "__init__.py"
    driver = tmp_path / "share/drivers/cephfs/driver.py"
    driver.parent.mkdir(parents=True)
    driver.write_text(
        "class NFSClusterProtocolHelper(Base):" if supports_cluster else ""
    )
    monkeypatch.setattr(
        manila.importlib.util, "find_spec", lambda _: Mock(origin=str(package))
    )

    if supports_cluster:
        manila._check_cluster_support()
    else:
        with pytest.raises(RuntimeError, match="MANILA_CEPHFS_NFS_MODE=standalone"):
            manila._check_cluster_support()


def test_standalone_mode_removes_cluster_setting(tmp_path, monkeypatch):
    """An empty cluster ID still selects the cluster helper, so delete the option."""
    monkeypatch.setattr(manila, "GANESHA_CONF", tmp_path / "ganesha.conf")
    monkeypatch.setattr(manila, "GANESHA_EXPORTS", tmp_path / "exports")
    monkeypatch.setattr(manila.ceph, "ensure_pool", Mock())
    run = Mock(return_value=manila.GANESHA_INDEX)
    monkeypatch.setattr(manila.core_utils, "run", run)
    monkeypatch.setattr(manila.core_utils, "my_ip", lambda: "192.0.2.10")
    monkeypatch.setattr(manila.core_utils, "restart_service", Mock())
    monkeypatch.setattr(manila.core_utils, "enable_service", Mock())

    settings = manila._configure_standalone_ganesha()

    assert "cephfs_nfs_cluster_id" not in settings
    run.assert_any_call(
        "crudini", ["--del", manila.CONF, manila.SHARE_TYPE, "cephfs_nfs_cluster_id"]
    )
