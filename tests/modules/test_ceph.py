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
