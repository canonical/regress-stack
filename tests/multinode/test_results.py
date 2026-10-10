# Copyright 2026 - Canonical Ltd
# SPDX-License-Identifier: GPL-3.0-only

import os
import shlex
import sys
import threading

import pytest

from regress_stack.core.deployment import activate
from regress_stack.core.utils import system
from regress_stack.core.results import result_line


@pytest.mark.parametrize(
    "line",
    [
        "{0} tempest.api.example.test_one [1.234s] ... ok",
        "{1} tempest.api.example.test_two [0.125s] ... FAILED",
        "Totals",
        "Ran: 3 tests in 1.3590 sec.",
        " - Passed: 1",
        " - Skipped: 1",
        " - Failed: 1",
        "Sum of execute time for each test: 1.3590 sec.",
    ],
)
def test_native_result_lines_are_preserved(line):
    assert result_line(line + "\n") == line


def test_skip_reason_and_attachments_are_not_published():
    assert result_line("{1} tempest.test_skip ... SKIPPED: private reason") == (
        "{1} tempest.test_skip ... SKIPPED"
    )
    for line in (
        "password = a-private-value",
        "request body with another-private-value",
        "Traceback containing a-private-value",
        "::error::workflow command",
    ):
        assert result_line(line) is None


@pytest.mark.parametrize("status", [0, 7])
def test_actual_system_path_preserves_results_and_failure_status(
    context, tmp_path, capsys, status
):
    script = (
        "import sys; "
        "print('{0} tempest.test_one [0.1s] ... ok'); "
        "print('{0} tempest.test_two [0.2s] ... FAILED', file=sys.stderr); "
        "print('request body admin_secret'); "
        "print('{0} tempest.test_admin_secret [0.1s] ... ok'); "
        "print(' - Passed: 1'); print(' - Failed: 1'); "
        f"sys.exit({status})"
    )
    with activate(context):
        result = system(shlex.join([sys.executable, "-c", script]), cwd=str(tmp_path))
    assert result == status
    output = capsys.readouterr().out
    assert "tempest.test_one" in output and "tempest.test_two" in output
    assert " - Passed: 1" in output and " - Failed: 1" in output
    assert "admin_secret" not in output and "request body" not in output


def test_results_are_visible_before_the_subprocess_finishes(
    context, tmp_path, monkeypatch
):
    seen = threading.Event()
    gate = tmp_path / "release"
    statuses = []

    class Console:
        def write(self, text):
            if "tempest.test_first" in text:
                seen.set()
            return len(text)

        def flush(self):
            pass

    monkeypatch.setattr(sys, "stdout", Console())
    script = (
        "import pathlib,sys,time\n"
        "print('{0} tempest.test_first [0.1s] ... ok', flush=True)\n"
        "while not pathlib.Path(sys.argv[1]).exists(): time.sleep(0.01)\n"
        "print(' - Passed: 1', flush=True)\n"
    )

    def worker():
        with activate(context):
            statuses.append(
                system(
                    shlex.join([sys.executable, "-c", script, str(gate)]),
                    env=os.environ.copy(),
                )
            )

    thread = threading.Thread(target=worker)
    thread.start()
    try:
        assert seen.wait(5), "Test outcome was not streamed"
        assert thread.is_alive(), "Output arrived only after process completion"
    finally:
        gate.touch()
        thread.join(5)
    assert not thread.is_alive()
    assert statuses == [0]
