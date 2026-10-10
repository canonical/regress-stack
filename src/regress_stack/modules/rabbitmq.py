# Copyright 2025 - Canonical Ltd
# SPDX-License-Identifier: GPL-3.0-only

import json
import logging

from regress_stack import topology
from regress_stack.core import utils as core_utils

LOG = logging.getLogger(__name__)

PACKAGES = ["rabbitmq-server"]
LOGS = ["/var/log/rabbitmq/"]

VHOST = "openstack"


def setup():
    LOG.debug("Setting up RabbitMQ...")
    ensure_vhost(VHOST)


def transport_url(username: str, password: str):
    hosts = ",".join(
        f"{username}:{password}@{host}:5672" for host in topology.messaging_hosts()
    )
    return f"rabbit://{hosts}/{VHOST}"


def ensure_vhost(name: str):
    LOG.debug("Ensuring RabbitMQ vhost %r exists...", name)
    output = core_utils.run("rabbitmqctl", ["list_vhosts", "--formatter", "json"])
    for vhost in json.loads(output):
        if vhost["name"] == name:
            return
    core_utils.run("rabbitmqctl", ["add_vhost", name])


def ensure_service(name: str):
    if topology.explicit():
        return topology.messaging_account(name)
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
