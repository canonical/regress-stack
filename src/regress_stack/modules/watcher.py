# Copyright 2026 - Canonical Ltd
# SPDX-License-Identifier: GPL-3.0-only

import logging
import pathlib

from regress_stack.core import utils as core_utils
from regress_stack.modules import keystone, mysql, nova, rabbitmq
from regress_stack.modules import utils as module_utils

LOG = logging.getLogger(__name__)

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

    _stop_services()

    core_utils.sudo("watcher-db-manage", ["upgrade"], user=SERVICE)
    _start_services()


def _stop_services():
    """Stop the watcher services.

    Hibiscus watcher-api has no standalone unit (WSGI under apache2), so
    only stop the services that exist.
    """
    for service in SERVICES:
        if core_utils.has_service(service):
            core_utils.run("systemctl", ["stop", service])


def _start_services():
    """Start the watcher services.

    Hibiscus watcher (>= 17.0.0) serves the API via apache2 (Listen 9322)
    instead of a standalone watcher-api unit. Reload apache2 to pick up the
    watcher.conf as a full restart would bounce other WSGI services, otherwise
    restart the unit.

    The decision engine and applier remain standalone daemons.
    """
    if core_utils.has_service("watcher-api"):
        core_utils.restart_service("watcher-api")
        LOG.info("Using standalone watcher-api unit")
    else:
        core_utils.graceful_apache()
        LOG.info("Using apache2 WSGI for watcher-api")

    for service in SERVICES:
        if service != "watcher-api":
            core_utils.restart_service(service)

            # Ensure the service is active before returning, as the decision engine and
            # applier are not WSGI services and do not have a healthcheck endpoint.
            core_utils.run("systemctl", ["is-active", "--quiet", service])

            LOG.info("Started %s", service)


def configure_tempest(tempest_conf: pathlib.Path):
    module_utils.cfg_set(
        str(tempest_conf),
        ("service_available", "watcher", "True"),
        # tempestconf does not set a microversion range; the watcher plugin
        # reads it from [optimize], and versioned tests skip when unset.
        ("optimize", "min_microversion", "1.0"),
        ("optimize", "max_microversion", "latest"),
    )
