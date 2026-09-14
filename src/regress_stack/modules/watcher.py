# Copyright 2026 - Canonical Ltd
# SPDX-License-Identifier: GPL-3.0-only

import pathlib

from regress_stack.core import utils as core_utils
from regress_stack.modules import keystone, mysql, nova, rabbitmq
from regress_stack.modules import utils as module_utils

DEPENDENCIES = {keystone, mysql, nova, rabbitmq}
BASE_PACKAGES = [
    "watcher-api",
    "watcher-decision-engine",
    "watcher-applier",
    "python3-watcherclient",
]
TEMPEST_PACKAGES = ["watcher-tempest-plugin"]
LOGS = ["/var/log/watcher/"]

CONF = "/etc/watcher/watcher.conf"
URL = f"http://{core_utils.my_ip()}:9322"
SERVICE = "watcher"
SERVICE_TYPE = "infra-optim"
SERVICES = ("watcher-api", "watcher-decision-engine", "watcher-applier")

TEST_INCLUDE_REGEXES = [r"watcher_tempest_plugin.tests.api"]


def determine_packages(no_tempest: bool = False) -> list[str]:
    packages = list(BASE_PACKAGES)
    if not no_tempest:
        packages.extend(TEMPEST_PACKAGES)
    return packages


def setup():
    db_user, db_pass = mysql.ensure_service(SERVICE)
    rabbit_user, rabbit_pass = rabbitmq.ensure_service(SERVICE)
    username, password = keystone.ensure_service_account(SERVICE, SERVICE_TYPE, URL)

    module_utils.cfg_set(
        CONF,
        (
            "database",
            "connection",
            mysql.connection_string(SERVICE, db_user, db_pass),
        ),
        ("database", "max_pool_size", "1"),
        ("DEFAULT", "transport_url", rabbitmq.transport_url(rabbit_user, rabbit_pass)),
        ("api", "host", core_utils.my_ip()),
        *module_utils.dict_to_cfg_set_args(
            "keystone_authtoken", keystone.authtoken_service(username, password)
        ),
        *module_utils.dict_to_cfg_set_args(
            "watcher_clients_auth", keystone.account_dict(username, password)
        ),
        ("oslo_messaging_notifications", "driver", "messagingv2"),
    )

    # Package installation starts the daemons before the database schema is
    # initialized.  Stop them to prevent the decision engine from racing the
    # migration and holding metadata locks on partially-created tables.
    for service in SERVICES:
        core_utils.run("systemctl", ["stop", service])

    module_utils.bootstrap_sudo("watcher-db-manage", ["upgrade"], user=SERVICE)
    for service in SERVICES:
        core_utils.restart_service(service)
        core_utils.run("systemctl", ["is-active", "--quiet", service])


def configure_tempest(tempest_conf: pathlib.Path):
    module_utils.cfg_set(
        str(tempest_conf),
        ("service_available", "watcher", "True"),
    )
