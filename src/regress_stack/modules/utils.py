# Copyright 2025 - Canonical Ltd
# SPDX-License-Identifier: GPL-3.0-only

import logging
import typing

from regress_stack.core import utils as core_utils

LOG = logging.getLogger(__name__)

PACKAGES = ["crudini"]
LOGS = [
    "/var/log/apache2/",
]

REGION = "AutoPkgOne"


def setup():
    pass


def cfg_set(config_file: str, *args: typing.Tuple[str, str, str]) -> None:
    for section, key, value in args:
        core_utils.run("crudini", ["--set", config_file, section, key, value])


def dict_to_cfg_set_args(
    section: str, d: typing.Dict[str, str]
) -> typing.List[typing.Tuple[str, str, str]]:
    return [(section, k, v) for k, v in d.items()]


def cfg_get(config_file: str, section: str, key: str) -> str:
    return core_utils.run("crudini", ["--get", config_file, section, key])
