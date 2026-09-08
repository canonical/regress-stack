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
        core_utils.run("crudini", ["--set", config_file, section, key, endpoint(value)])


def dict_to_cfg_set_args(
    section: str, d: typing.Dict[str, str]
) -> typing.List[typing.Tuple[str, str, str]]:
    return [(section, k, v) for k, v in d.items()]


def cfg_get(config_file: str, section: str, key: str) -> str:
    return core_utils.run("crudini", ["--get", config_file, section, key])


def bootstrap() -> bool:
    from regress_stack.core.deployment import current, bootstrap_only

    context = current()
    return context is None or context.bootstrap or not bootstrap_only()


def bootstrap_sudo(cmd, args, user=None):
    if bootstrap():
        return core_utils.sudo(cmd, args, user=user)


def endpoint(value: str) -> str:
    from urllib.parse import urlsplit, urlunsplit
    from regress_stack.core.deployment import current

    context = current()
    if context is None or not value.startswith("http://"):
        return value
    parsed = urlsplit(value)
    from regress_stack.multinode.access import API_PORTS

    port = parsed.port
    if port not in API_PORTS:
        return value
    if len(context.deployment.controllers) == 3:
        port += 10000
    return urlunsplit(
        (
            parsed.scheme,
            f"{context.deployment.api_address}:{port}",
            parsed.path,
            parsed.query,
            parsed.fragment,
        )
    )


def preseed_value(key, default):
    from regress_stack.core.deployment import current

    context = current()
    return context.secret(key) if context else default
