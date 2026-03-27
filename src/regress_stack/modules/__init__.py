# Copyright 2025 - Canonical Ltd
# SPDX-License-Identifier: GPL-3.0-only

BASE_PACKAGES = ["python3-openstackclient", "python3-tempestconf"]
TEMPEST_PACKAGES = ["tempest"]


def determine_packages(no_tempest: bool = False) -> list[str]:
    packages = list(BASE_PACKAGES)
    if not no_tempest:
        packages.extend(TEMPEST_PACKAGES)
    return packages
