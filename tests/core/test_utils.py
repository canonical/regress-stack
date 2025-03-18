# Copyright 2025 - Canonical Ltd
# SPDX-License-Identifier: GPL-3.0-only
import os
import unittest.mock as mock

import pytest

import regress_stack.core.utils


@pytest.fixture
def mock_cpu_count(monkeypatch):
    cpu_count = mock.Mock(return_value=42 * 3)

    monkeypatch.setattr("regress_stack.core.utils.multiprocessing.cpu_count", cpu_count)
    yield cpu_count


@pytest.fixture
def mock_environ(monkeypatch):
    environ = {}

    monkeypatch.setattr("regress_stack.core.utils.os.environ", environ)
    yield environ


@pytest.fixture
def mock_system(monkeypatch):
    system = mock.Mock()

    monkeypatch.setattr("regress_stack.core.utils.os.system", system)
    yield system


@pytest.fixture
def mock_chdir(monkeypatch):
    chdir = mock.Mock()

    monkeypatch.setattr("regress_stack.core.utils.os.chdir", chdir)
    yield chdir


def test_concurrency_cb(mock_cpu_count):
    assert type(regress_stack.core.utils.concurrency_cb("auto")) is int
    assert regress_stack.core.utils.concurrency_cb("auto") == 42
    assert type(regress_stack.core.utils.concurrency_cb("51")) is int
    assert regress_stack.core.utils.concurrency_cb("51") == 51
    with pytest.raises(ValueError):
        regress_stack.core.utils.concurrency_cb("NotInt")


def test_system(mock_environ, mock_system, mock_chdir):
    mock_system.return_value = 10752
    assert regress_stack.core.utils.system("abc") == 42
    mock_system.assert_called_once_with("abc")
    mock_chdir.assert_not_called()
    mock_system.reset()
    regress_stack.core.utils.system("abc", {"a": "A"})
    mock_system.assert_called_with("abc")
    mock_chdir.assert_not_called()
    assert "a" in mock_environ
    mock_system.reset()
    regress_stack.core.utils.system("abc", {"b": "B"}, "/non-existent")
    mock_system.assert_called_with("abc")
    assert "b" in mock_environ
    mock_chdir.assert_has_calls(
        [
            mock.call("/non-existent"),
            mock.call(os.getcwd()),
        ]
    )
