# Copyright 2026 - Canonical Ltd
# SPDX-License-Identifier: GPL-3.0-only

import itertools
import json
from unittest.mock import patch

import pytest

from regress_stack.modules import ceph


@pytest.fixture
def filesystem_setup():
    """Isolate filesystem setup commands and its monotonic readiness deadline."""
    with (
        patch.object(ceph, "Path"),
        patch.object(ceph.shutil, "chown"),
        patch.object(ceph.core_utils, "fqdn", return_value="test-host"),
        patch.object(ceph.core_utils, "run") as run,
        patch.object(ceph.time, "sleep") as sleep,
        patch.object(ceph.time, "monotonic") as monotonic,
    ):
        yield run, sleep, monotonic


def test_filesystem_waits_for_slow_mds_initialization(filesystem_setup):
    """Allow a creating MDS to become active beyond the former one-minute wait."""
    run, sleep, monotonic = filesystem_setup
    statuses = iter(["creating"] * 40 + ["active"])

    def command_output(command, args):
        if args[:2] == ["fs", "ls"]:
            return json.dumps([{"name": "test-fs"}])
        if args[:2] == ["fs", "status"]:
            return json.dumps({"mdsmap": [{"state": next(statuses)}]})
        return ""

    run.side_effect = command_output
    monotonic.side_effect = itertools.count(0, 2)

    ceph.ensure_filesystem("test-fs")

    assert sleep.call_count == 40


def test_filesystem_timeout_reports_last_mds_state(filesystem_setup):
    """Bound startup waits and retain the last MDS state in timeout diagnostics."""
    run, sleep, monotonic = filesystem_setup

    def command_output(command, args):
        if args[:2] == ["fs", "ls"]:
            return json.dumps([{"name": "test-fs"}])
        if args[:2] == ["fs", "status"]:
            return json.dumps({"mdsmap": [{"state": "creating"}]})
        return ""

    run.side_effect = command_output
    monotonic.side_effect = [0, ceph.MDS_START_TIMEOUT]

    with pytest.raises(RuntimeError, match="300 seconds.*creating"):
        ceph.ensure_filesystem("test-fs")

    sleep.assert_not_called()


@pytest.mark.parametrize("packaged_files_remain", [False, True])
def test_adopted_manager_is_not_recreated(tmp_path, monkeypatch, packaged_files_remain):
    """Prefer adopted keyring and moved marker even when stale packaged files remain."""
    managed = tmp_path / "managed-mgr"
    managed.mkdir()
    (managed / "keyring").touch()
    # cephadm moves the original setup marker into the adopted directory.
    (managed / "done").touch()
    keyring, done = tmp_path / "packaged-keyring", tmp_path / "packaged-done"
    if packaged_files_remain:
        keyring.touch()
        done.touch()
    monkeypatch.setattr(ceph, "_managed_mgr_folder", lambda: managed)
    monkeypatch.setattr(ceph, "MGR_KEYRING", keyring)
    monkeypatch.setattr(ceph, "MGR_SETUP_DONE", done)
    with patch.object(ceph.core_utils, "run") as run:
        assert ceph.setup_mgr_keyring() == managed / "keyring"
        assert ceph.setup_mgr().is_file()
        assert ceph.setup_mgr() == managed / "done"
    run.assert_not_called()


def test_packaged_manager_setup_is_cached(tmp_path, monkeypatch):
    """Existing package keyring and marker avoid rerunning Manager setup commands."""
    keyring, done = tmp_path / "keyring", tmp_path / "done"
    keyring.touch()
    done.touch()
    monkeypatch.setattr(ceph, "_managed_mgr_folder", lambda: tmp_path / "not-adopted")
    monkeypatch.setattr(ceph, "MGR_KEYRING", keyring)
    monkeypatch.setattr(ceph, "MGR_SETUP_DONE", done)
    with patch.object(ceph.core_utils, "run") as run:
        assert ceph.setup_mgr_keyring() == keyring
        assert ceph.setup_mgr() == done
    run.assert_not_called()


@pytest.mark.parametrize("cluster_output", ['["test-nfs"]', "test-nfs\n"])
@pytest.mark.parametrize(
    "existing_keys", ["", "ssh-ed25519 existing", "ssh-rsa cephadm\n"]
)
def test_nfs_cluster_reuses_existing_deployment(
    tmp_path, monkeypatch, cluster_output, existing_keys
):
    """Existing clusters and SSH keys survive setup on JSON and Quincy text output."""
    managed = tmp_path / "managed-mgr"
    managed.mkdir()
    keys = tmp_path / "authorized_keys"
    keys.write_text(existing_keys)
    monkeypatch.setattr(ceph, "_managed_mgr_folder", lambda: managed)
    monkeypatch.setattr(ceph, "Path", lambda _: keys)
    monkeypatch.setattr(ceph.core_utils, "my_ip", lambda: "192.0.2.10")

    def command_output(command, args):
        if args == ["mgr", "metadata", "-f", "json"]:
            return json.dumps(
                [
                    {
                        "name": ceph.core_utils.fqdn(),
                        "container_image": "quay.io/ceph/ceph:v17",
                    }
                ]
            )
        if args == ["version"]:
            return "ceph version 17.2.9 (hash) quincy (stable)"
        if args == ["cephadm", "get-pub-key"]:
            return "ssh-rsa cephadm\n"
        if args[:3] == ["nfs", "cluster", "ls"]:
            return cluster_output
        if args[:2] == ["orch", "ps"]:
            return json.dumps(
                [{"daemon_name": "nfs.test-nfs.host", "status_desc": "running"}]
            )
        if args[:3] == ["nfs", "cluster", "info"]:
            return json.dumps({"test-nfs": {"backend": [{"ip": "192.0.2.10"}]}})
        return ""

    with (
        patch.object(ceph.core_utils, "run", side_effect=command_output) as run,
        patch.object(ceph.core_utils, "enable_service"),
        patch.object(ceph.core_utils, "restart_service"),
    ):
        ceph.ensure_nfs_cluster("test-nfs")

    assert keys.read_text().splitlines() == list(
        dict.fromkeys([*existing_keys.splitlines(), "ssh-rsa cephadm"])
    )
    assert not any(c.args[0] == "cephadm" for c in run.call_args_list)
    assert not any(
        c.args[1][:3] == ["nfs", "cluster", "create"] for c in run.call_args_list
    )


@pytest.mark.parametrize(
    "version", ["", "ceph version", "ceph version unknown", "other 20.2.0"]
)
def test_nfs_cluster_rejects_unrecognized_version(version):
    """Report malformed version output before selecting an image or adopting services."""
    with patch.object(ceph.core_utils, "run", return_value=version) as run:
        with pytest.raises(
            RuntimeError, match="Cannot determine the Ceph image release"
        ):
            ceph.ensure_nfs_cluster("test-nfs")
    run.assert_called_once_with("ceph", ["version"])
