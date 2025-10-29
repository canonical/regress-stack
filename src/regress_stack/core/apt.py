# Copyright 2025 - Canonical Ltd
# SPDX-License-Identifier: GPL-3.0-only

import typing

import apt
import apt_pkg


APT_CACHE: typing.Optional[apt.Cache] = None

# Initialize the apt_pkg module, otherwise some functions will not return the
# expected value.
apt_pkg.init()


def get_cache() -> apt.Cache:
    global APT_CACHE

    if APT_CACHE is None:
        APT_CACHE = apt.Cache()

    return APT_CACHE


def pkgs_installed(pkgs: typing.List[str]) -> bool:
    apt_cache = get_cache()

    try:
        return all([apt_cache[pkg].is_installed for pkg in pkgs])
    except KeyError:
        return False


def get_pkg_version(pkg: str) -> typing.Optional[str]:
    apt_cache = get_cache()

    try:
        pkg_version = apt_cache[pkg].installed
    except KeyError:
        return None
    if pkg_version is None:
        return None
    return pkg_version.version


class PkgVersionCompare:
    """Compare installed package version with given version strings."""

    def __init__(self, name: str, candidate: bool = False) -> None:
        """
        Initialize the PkgVersionCompare object.
        :param name: Name of the package to compare.
        :param candidate: If True, compare with the candidate version.
                          If False, compare with the installed version.
        """
        apt_cache = apt.Cache()
        apt_cache.open()
        try:
            if candidate:
                self.version = apt_cache[name].candidate.version
            else:
                self.version = apt_cache[name].installed.version
        except KeyError:
            if candidate:
                raise ValueError(f"Package {name} has no candidate version")
            else:
                raise ValueError(f"Package {name} is not installed")

    def __lt__(self, other: str) -> bool:
        cmp = apt_pkg.version_compare(self.version, other)
        return cmp < 0

    def __eq__(self, other: str) -> bool:
        cmp = apt_pkg.version_compare(self.version, other)
        return cmp == 0

    def __ge__(self, other: str) -> bool:
        cmp = apt_pkg.version_compare(self.version, other)
        return cmp >= 0

    def __ne__(self, other: str) -> bool:
        return not self.__eq__(other)
