# Copyright 2026 - Canonical Ltd
# SPDX-License-Identifier: GPL-3.0-only

from types import SimpleNamespace
from unittest.mock import Mock

from regress_stack.cli import utils


def test_collect_logs_includes_cephadm_subdirectories(tmp_path, monkeypatch, capsys):
    """Cephadm's FSID log directories must not make collection fail after Tempest."""
    log_dir = tmp_path / "ceph"
    nested = log_dir / "cluster-fsid"
    nested.mkdir(parents=True)
    (log_dir / "cephadm.log").write_text("host log\n")
    (nested / "ceph-mgr.log").write_text("container log\n")
    module = SimpleNamespace(__name__="ceph", LOGS=[str(log_dir)])
    monkeypatch.setattr(
        utils, "get_execution_order", lambda *_: [SimpleNamespace(module=module)]
    )
    monkeypatch.setattr(utils.utils, "run", Mock())

    utils.collect_logs()

    output = capsys.readouterr().out
    assert "host log" in output
    assert "container log" in output
