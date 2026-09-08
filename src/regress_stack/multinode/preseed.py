# Copyright 2026 - Canonical Ltd
# SPDX-License-Identifier: GPL-3.0-only

import base64
import dataclasses
import json
import os
import secrets
import uuid

from regress_stack.core.deployment import Secret, private_write
from regress_stack.multinode import common, coordination, storage


SERVICES = (
    "keystone",
    "barbican",
    "glance",
    "placement",
    "cinder",
    "neutron",
    "nova",
    "heat",
    "magnum",
    "watcher",
)


def generate(context):
    values = {}
    for prefix in ("mysql", "rabbitmq", "keystone"):
        for service in SERVICES:
            values[f"{prefix}/{service}"] = secrets.token_hex(24)
    for name in (
        "mysql/nova_api",
        "mysql/nova_cell0",
        "mysql/recovery",
        "mysql/check",
        "rabbitmq/cookie",
        "keystone/admin",
        "neutron/metadata",
        "coordination/password",
        "heat/domain",
        "magnum/domain",
    ):
        values[name] = secrets.token_hex(24)
    # MySQL recovery channels accept at most 32 characters. URL-safe base64
    # preserves 192 bits of entropy within that limit.
    values["mysql/recovery"] = secrets.token_urlsafe(24)
    # Tooz before 6.0 cannot authenticate to Sentinel; 6.0 requires the
    # same credentials as Redis. Freeze this choice for every controller.
    values["coordination/sentinel-password"] = (
        values["coordination/password"]
        if coordination.sentinel_auth_supported()
        else ""
    )
    values["coordination/implementation"] = coordination.implementation()
    values["barbican/kek"] = base64.urlsafe_b64encode(os.urandom(32)).decode()
    values["heat/auth_encryption_key"] = secrets.token_hex(16)
    for directory in ("fernet-keys", "credential-keys"):
        for number in ("0", "1"):
            values[f"keystone/{directory}/{number}"] = base64.urlsafe_b64encode(
                os.urandom(32)
            ).decode()
    from regress_stack.modules import cinder

    values["cinder/service-type"] = cinder.get_service_type()
    values["ceph/fsid"] = str(uuid.uuid4())
    values["ceph/rbd_uuid"] = str(uuid.uuid4())
    for name in [
        *storage.CAPABILITIES,
        *(f"mgr.{node.name}" for node in context.deployment.controllers),
    ]:
        values[f"ceph/{name}"] = common.run(
            "ceph-authtool", ["--gen-print-key"]
        ).strip()
    context.values.update(values)


def contributions(context):
    """Give controllers their setup state and computes only their client state."""
    controllers = frozenset(node.name for node in context.deployment.controllers)
    all_nodes = frozenset(node.name for node in context.deployment.nodes)
    compute_keys = {
        "rabbitmq/nova",
        "keystone/nova",
        "neutron/metadata",
        "ceph/fsid",
        "ceph/rbd_uuid",
        "ceph/client.volumes",
        "cinder/service-type",
    }
    result = {}
    for key, value in context.values.items():
        if key.startswith("coordination/"):
            continue
        if key.startswith("ceph/mgr."):
            recipients = frozenset({key.removeprefix("ceph/mgr.")})
        else:
            recipients = all_nodes if key in compute_keys else controllers
        result[key] = Secret(value, recipients)
    result.update(coordination.contribute(context))
    return result


def save(context, path):
    private_write(
        path,
        json.dumps(
            {
                "schema": 1,
                "deployment": dataclasses.asdict(context.deployment),
                "deployment_id": context.deployment_id,
                "local_name": context.local_name,
                "values": context.values,
            },
            indent=2,
        )
        + "\n",
    )
