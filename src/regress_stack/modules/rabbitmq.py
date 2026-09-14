# Copyright 2025 - Canonical Ltd
# SPDX-License-Identifier: GPL-3.0-only

import json
import logging

from regress_stack.core import utils as core_utils

LOG = logging.getLogger(__name__)

PACKAGES = ["rabbitmq-server"]
LOGS = ["/var/log/rabbitmq/"]

VHOST = "openstack"


def setup():
    LOG.debug("Setting up RabbitMQ...")
    ensure_vhost(VHOST)


def transport_url(username: str, password: str):
    from regress_stack.core.deployment import current

    context = current()
    if context is not None:
        hosts = ",".join(
            f"{username}:{password}@{node.address}:5672"
            for node in context.deployment.controllers
        )
        return f"rabbit://{hosts}/{VHOST}"
    return f"rabbit://{username}:{password}@localhost:5672/{VHOST}"


def ensure_vhost(name: str):
    LOG.debug("Ensuring RabbitMQ vhost %r exists...", name)
    output = core_utils.run("rabbitmqctl", ["list_vhosts", "--formatter", "json"])
    for vhost in json.loads(output):
        if vhost["name"] == name:
            return
    core_utils.run("rabbitmqctl", ["add_vhost", name])


def ensure_service(name: str):
    from regress_stack.core.deployment import current

    if current() is not None:
        from regress_stack.multinode.messaging import (
            ensure_service as clustered_service,
        )

        return clustered_service(name)
    password = "changeme"
    ensure_user(name, password)
    ensure_permissions(name, VHOST)
    return name, password


def ensure_user(name: str, password: str):
    LOG.debug("Ensuring RabbitMQ user %r exists...", name)
    output = core_utils.run("rabbitmqctl", ["list_users", "--formatter", "json"])
    for user in json.loads(output):
        if user["user"] == name:
            return
    core_utils.run("rabbitmqctl", ["add_user", name, password])


def ensure_permissions(user: str, vhost: str):
    LOG.debug("Ensuring RabbitMQ user %r has permissions on %r...", user, vhost)
    core_utils.run(
        "rabbitmqctl", ["set_permissions", "--vhost", vhost, user, ".*", ".*", ".*"]
    )
