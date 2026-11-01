# Copyright 2026 - Canonical Ltd
# SPDX-License-Identifier: GPL-3.0-only

import subprocess

import pytest

from regress_stack.modules import magnum


def _fake_run(failures):
    calls = []

    def run(cmd, args=(), cwd=None, env=None):
        calls.append((cmd, list(args), cwd))
        if len(calls) <= failures:
            raise subprocess.CalledProcessError(4, [cmd, *args])
        return ""

    return run, calls


def test_download_retries_a_failed_attempt(monkeypatch):
    run, calls = _fake_run(failures=1)
    monkeypatch.setattr(magnum.core_utils, "run", run)
    monkeypatch.setattr(magnum.time, "sleep", lambda _seconds: None)

    magnum._download("https://example.com/image.qcow2.xz", "image.qcow2.xz", "/tmp")

    assert len(calls) == 2
    assert calls[-1] == (
        "wget",
        ["--timeout=60", "-O", "image.qcow2.xz", "https://example.com/image.qcow2.xz"],
        "/tmp",
    )


def test_download_gives_up_after_the_last_attempt(monkeypatch):
    run, calls = _fake_run(failures=magnum.COREOS_DOWNLOAD_ATTEMPTS)
    monkeypatch.setattr(magnum.core_utils, "run", run)
    monkeypatch.setattr(magnum.time, "sleep", lambda _seconds: None)

    with pytest.raises(subprocess.CalledProcessError):
        magnum._download("https://example.com/image.qcow2.xz", "image.qcow2.xz", "/tmp")

    assert len(calls) == magnum.COREOS_DOWNLOAD_ATTEMPTS
