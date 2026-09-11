# Copyright 2026 - Canonical Ltd
# SPDX-License-Identifier: GPL-3.0-only

from __future__ import annotations

import json
from typing import TypedDict

from regress_stack.multinode import common


class _Vhost(TypedDict):
    name: str


class _User(TypedDict):
    user: str


def setup() -> None:
    context = common.context()
    if common.done("rabbitmq"):
        return
    common.run("systemctl", ["stop", "rabbitmq-server"])
    common.write(
        "/var/lib/rabbitmq/.erlang.cookie",
        common.token(context.secret("rabbitmq/cookie")),
        user="rabbitmq",
        mode=0o400,
    )
    common.write(
        "/etc/rabbitmq/rabbitmq-env.conf",
        f"NODENAME=rabbit@{context.local.name}\n",
        mode=0o644,
    )
    common.write(
        "/etc/rabbitmq/rabbitmq.conf",
        f"listeners.tcp.1 = {context.local.address}:5672\n",
        mode=0o644,
    )
    common.restart("rabbitmq-server")
    if not context.bootstrap:
        common.run("rabbitmqctl", ["stop_app"])
        # This path is only entered on a fresh, explicitly preseeded machine.
        common.run("rabbitmqctl", ["reset"])
        common.run(
            "rabbitmqctl",
            ["join_cluster", f"rabbit@{context.deployment.bootstrap.name}"],
        )
        common.run("rabbitmqctl", ["start_app"])
        common.run("rabbitmq-queues", ["grow", f"rabbit@{context.local.name}", "all"])
    else:
        vhosts: list[_Vhost] = json.loads(
            common.run("rabbitmqctl", ["list_vhosts", "--formatter", "json"])
        )
        if not any(vhost["name"] == "openstack" for vhost in vhosts):
            common.run("rabbitmqctl", ["add_vhost", "openstack"])
    common.mark("rabbitmq")


def ensure_service(name: str) -> tuple[str, str]:
    context = common.context()
    name = common.token(name)
    password = common.token(context.secret(f"rabbitmq/{name}"))
    if context.bootstrap:
        users: list[_User] = json.loads(
            common.run("rabbitmqctl", ["list_users", "--formatter", "json"])
        )
        if not any(user["user"] == name for user in users):
            common.run("rabbitmqctl", ["add_user", name, password])
        common.run(
            "rabbitmqctl",
            ["set_permissions", "--vhost", "openstack", name, ".*", ".*", ".*"],
        )
    return name, password
