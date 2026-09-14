# Copyright 2025 - Canonical Ltd
# SPDX-License-Identifier: GPL-3.0-only
import subprocess
import unittest.mock as mock

import pytest

import regress_stack.core.utils


@pytest.fixture
def mock_cpu_count(monkeypatch):
    cpu_count = mock.Mock(return_value=42 * 3)

    monkeypatch.setattr("regress_stack.core.utils.multiprocessing.cpu_count", cpu_count)
    yield cpu_count


@pytest.fixture
def mock_os(monkeypatch):
    os = mock.Mock()

    os.environ = {}

    monkeypatch.setattr("regress_stack.core.utils.os", os)
    yield os


def test_concurrency_cb(mock_cpu_count):
    assert type(regress_stack.core.utils.concurrency_cb("auto")) is int
    assert regress_stack.core.utils.concurrency_cb("auto") == 42
    assert type(regress_stack.core.utils.concurrency_cb("51")) is int
    assert regress_stack.core.utils.concurrency_cb("51") == 51
    with pytest.raises(ValueError):
        regress_stack.core.utils.concurrency_cb("NotInt")


def test_system(mock_os):
    mock_os.waitstatus_to_exitcode.return_value = 42
    assert regress_stack.core.utils.system("abc") == 42
    mock_os.system.assert_called_once_with("abc")
    mock_os.chdir.assert_not_called()
    assert mock_os.environ == {}
    mock_os.reset()
    regress_stack.core.utils.system("abc", {"a": "A"})
    mock_os.system.assert_called_with("abc")
    mock_os.chdir.assert_not_called()
    assert "a" in mock_os.environ
    mock_os.reset()
    regress_stack.core.utils.system("abc", {"b": "B"}, "/non-existent")
    mock_os.system.assert_called_with("abc")
    assert "b" in mock_os.environ
    mock_os.chdir.assert_has_calls(
        [
            mock.call("/non-existent"),
            mock.call(mock_os.getcwd()),
        ]
    )
    mock_os.reset()


def test_tempest_version_parses_full(monkeypatch):
    monkeypatch.setattr(
        regress_stack.core.utils, "run", lambda *_args, **_kwargs: "tempest 44.0.0\n"
    )
    assert regress_stack.core.utils.tempest_version() == (44, 0, 0)


def test_tempest_version_parses_missing_patch(monkeypatch):
    monkeypatch.setattr(
        regress_stack.core.utils, "run", lambda *_args, **_kwargs: "tempest 42.1\n"
    )
    assert regress_stack.core.utils.tempest_version() == (42, 1, 0)


def test_tempest_version_file_not_found(monkeypatch):
    def raise_file_not_found(*_args, **_kwargs):
        raise FileNotFoundError

    monkeypatch.setattr(regress_stack.core.utils, "run", raise_file_not_found)
    assert regress_stack.core.utils.tempest_version() is None


def test_tempest_version_called_process_error(monkeypatch):
    def raise_called_process_error(*_args, **_kwargs):
        raise subprocess.CalledProcessError(1, ["tempest", "--version"])

    monkeypatch.setattr(regress_stack.core.utils, "run", raise_called_process_error)
    assert regress_stack.core.utils.tempest_version() is None


def test_tempest_version_unparseable(monkeypatch):
    monkeypatch.setattr(
        regress_stack.core.utils, "run", lambda *_args, **_kwargs: "tempest\n"
    )
    assert regress_stack.core.utils.tempest_version() is None


def test_parse_tempest_version_accepts_missing_patch():
    assert regress_stack.core.utils._parse_tempest_version("tempest 42.1\n") == (
        42,
        1,
        0,
    )


def test_multinode_system_preserves_unbounded_execution(monkeypatch):
    monkeypatch.setattr("regress_stack.core.deployment.current", lambda: object())
    runner = mock.Mock(return_value="")
    monkeypatch.setattr("regress_stack.multinode.common.run", runner)

    assert regress_stack.core.utils.system("tempest run", {"A": "B"}, "/work") == 0
    runner.assert_called_once_with(
        "sh", ["-c", "tempest run"], env={"A": "B"}, cwd="/work", timeout=None
    )


def test_multinode_system_preserves_failure_status(monkeypatch):
    monkeypatch.setattr("regress_stack.core.deployment.current", lambda: object())
    runner = mock.Mock(side_effect=subprocess.CalledProcessError(3, ["sh"]))
    monkeypatch.setattr("regress_stack.multinode.common.run", runner)

    assert regress_stack.core.utils.system("tempest run") == 3
