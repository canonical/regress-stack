# Copyright 2026 - Canonical Ltd
# SPDX-License-Identifier: GPL-3.0-only

from __future__ import annotations

import base64
from collections.abc import Callable
import os
import secrets
import uuid

from regress_stack.core.deployment import Context, Secret
from regress_stack.modules import cinder
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


def _token() -> str:
    return secrets.token_hex(24)


def _key() -> str:
    return base64.urlsafe_b64encode(os.urandom(32)).decode()


def _uuid() -> str:
    return str(uuid.uuid4())


def _ceph_key() -> str:
    return common.run("ceph-authtool", ["--gen-print-key"]).strip()


def generate(context: Context) -> dict[str, str]:
    """Generate every deployment secret on the bootstrap node."""
    deployment = context.deployment
    generators: dict[str, Callable[[], str]] = {
        **{
            f"{prefix}/{service}": _token
            for prefix in ("mysql", "rabbitmq", "keystone")
            for service in SERVICES
        },
        **dict.fromkeys(
            (
                "mysql/nova_api",
                "mysql/nova_cell0",
                "mysql/check",
                "rabbitmq/cookie",
                "keystone/admin",
                "neutron/metadata",
                "coordination/password",
                "heat/domain",
                "magnum/domain",
            ),
            _token,
        ),
        # MySQL recovery channels accept at most 32 characters. URL-safe base64
        # preserves 192 bits of entropy within that limit.
        "mysql/recovery": lambda: secrets.token_urlsafe(24),
        "coordination/implementation": coordination.implementation,
        "barbican/kek": _key,
        "heat/auth_encryption_key": lambda: secrets.token_hex(16),
        **{
            f"keystone/{directory}/{number}": _key
            for directory in ("fernet-keys", "credential-keys")
            for number in ("0", "1")
        },
    }
    if "cinder" not in deployment.disabled_modules:
        generators["cinder/service-type"] = cinder.get_service_type
    if "ceph" not in deployment.disabled_modules:
        generators["ceph/fsid"] = _uuid
        generators["ceph/rbd_uuid"] = _uuid
        for name in [
            *storage.CAPABILITIES,
            *(f"mgr.{node.name}" for node in deployment.controllers),
        ]:
            generators[f"ceph/{name}"] = _ceph_key
    values = {key: make() for key, make in generators.items()}
    # Tooz before 6.0 cannot authenticate to Sentinel; 6.0 requires the
    # same credentials as Redis. Freeze this choice for every controller.
    values["coordination/sentinel-password"] = (
        values["coordination/password"]
        if coordination.sentinel_auth_supported()
        else ""
    )
    return values


def contributions(context: Context) -> dict[str, Secret]:
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
    result: dict[str, Secret] = {}
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
