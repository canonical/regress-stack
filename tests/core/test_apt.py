# Copyright 2025 - Canonical Ltd
# SPDX-License-Identifier: GPL-3.0-only

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

import regress_stack.core.apt


class FakeCache(dict):
    def open(self):
        return None


@pytest.fixture
def mock_apt(monkeypatch):
    cache = FakeCache()
    apt = Mock(Cache=Mock(return_value=cache))

    monkeypatch.setattr("regress_stack.core.apt.apt", apt)
    yield apt


def test_get_cache(mock_apt):
    regress_stack.core.apt.APT_CACHE = None
    assert regress_stack.core.apt.get_cache() == mock_apt.Cache()
    assert regress_stack.core.apt.APT_CACHE == mock_apt.Cache()


def test_pkgs_installed(mock_apt):
    regress_stack.core.apt.APT_CACHE = None
    assert regress_stack.core.apt.pkgs_installed(["pkg"]) is False

    regress_stack.core.apt.APT_CACHE = None
    mock_apt.Cache()["pkg"] = Mock(is_installed=True)
    assert regress_stack.core.apt.pkgs_installed(["pkg"]) is True


def test_get_upstream_pkg_version(mock_apt):
    regress_stack.core.apt.APT_CACHE = None
    mock_apt.Cache()["pkg"] = Mock(
        installed=SimpleNamespace(version="3:32.0.0-0ubuntu1.1"),
        candidate=SimpleNamespace(version="3:33.0.0-0ubuntu1"),
    )
    assert regress_stack.core.apt.get_upstream_pkg_version("pkg") == "32.0.0"
    assert (
        regress_stack.core.apt.get_upstream_pkg_version("pkg", candidate=True)
        == "33.0.0"
    )


def test_pkg_version_compare_upstream(mock_apt):
    mock_apt.Cache()["pkg"] = Mock(
        installed=SimpleNamespace(version="3:32.0.0-0ubuntu1.1"),
        candidate=SimpleNamespace(version="3:33.0.0-0ubuntu1"),
    )
    assert regress_stack.core.apt.PkgVersionCompare("pkg", upstream=True) >= "32.0.0"
    assert (
        regress_stack.core.apt.PkgVersionCompare("pkg", candidate=True, upstream=True)
        >= "33.0.0"
    )


def test_pkg_version_compare_missing_installed_version(mock_apt):
    mock_apt.Cache()["pkg"] = Mock(
        installed=None, candidate=SimpleNamespace(version="1")
    )
    with pytest.raises(ValueError, match="Package pkg is not installed"):
        regress_stack.core.apt.PkgVersionCompare("pkg")


def test_pkg_version_compare_missing_candidate_version(mock_apt):
    mock_apt.Cache()["pkg"] = Mock(
        installed=SimpleNamespace(version="1"),
        candidate=None,
    )
    with pytest.raises(ValueError, match="Package pkg has no candidate version"):
        regress_stack.core.apt.PkgVersionCompare("pkg", candidate=True)
