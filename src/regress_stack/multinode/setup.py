# Copyright 2026 - Canonical Ltd
# SPDX-License-Identifier: GPL-3.0-only

from __future__ import annotations

from collections.abc import Iterator
import contextlib
import dataclasses
import fcntl
import json
import logging
import os
from pathlib import Path
import socket
from typing import TypedDict
import uuid

from regress_stack.core import utils
from regress_stack.core.deployment import Context, Deployment, activate
from regress_stack.multinode import (
    access,
    common,
    coordination,
    database,
    messaging,
    networking,
    preseed,
    profiles,
    services,
    storage,
)

LOCAL_STATE = common.STATE / "context.json"


class _AddressInfo(TypedDict, total=False):
    local: str
    scope: str


class _InterfaceInfo(TypedDict):
    ifname: str
    addr_info: list[_AddressInfo]


@contextlib.contextmanager
def quiet_context(context: Context, *, bootstrap_only: bool = True) -> Iterator[None]:
    # Third-party HTTP debug logging can contain authentication request bodies.
    # regress-stack's own logging stays enabled; private commands never log.
    levels = {
        name: logging.getLogger(name).level
        for name in ("openstack", "keystoneauth", "urllib3")
    }
    for name in levels:
        logging.getLogger(name).setLevel(logging.WARNING)
    try:
        with activate(context, bootstrap_only=bootstrap_only):
            yield
    finally:
        for name, level in levels.items():
            logging.getLogger(name).setLevel(level)


def load(
    inventory: Path | None = None,
    node: str | None = None,
    seed: Path | None = None,
) -> Context:
    """A validated context from an inventory and node name, or a preseed."""
    if seed is not None:
        if inventory is not None or node is not None:
            raise ValueError("Use --inventory with --node, or --preseed")
        return read_state(seed)
    if inventory is None or node is None:
        raise ValueError("Use --inventory with --node, or --preseed")
    return _validated(Context(Deployment.read(inventory), node, str(uuid.uuid4())))


def read_state(path: Path) -> Context:
    return _validated(Context.read(path))


def _validated(context: Context) -> Context:
    profiles.validate(context.deployment)
    return context


def local_preflight(context: Context) -> None:
    if os.geteuid() != 0:
        raise RuntimeError("Explicit setup must run as root on the target VM")
    if socket.gethostname().split(".")[0] != context.local.name:
        raise RuntimeError("Local hostname does not match the inventory")
    interfaces: list[_InterfaceInfo] = json.loads(
        common.run("ip", ["-j", "address", "show"])
    )
    by_name = {interface["ifname"]: interface for interface in interfaces}
    local = context.local
    if (
        local.management_interface not in by_name
        or local.provider_interface not in by_name
    ):
        raise RuntimeError("An inventory interface is missing on this VM")
    addresses = {
        entry.get("local") for entry in by_name[local.management_interface]["addr_info"]
    }
    if local.address not in addresses:
        raise RuntimeError(
            "Management address is not assigned to the inventory interface"
        )
    if any(
        entry.get("scope") == "global"
        for entry in by_name[local.provider_interface]["addr_info"]
    ):
        raise RuntimeError("The provider interface must have no host IP addresses")
    if not LOCAL_STATE.exists() and any(utils.REGRESS_STACK_DIR.glob("*.setup")):
        raise RuntimeError(
            "Explicit setup requires a fresh VM, not an existing legacy deployment"
        )
    profiles.verify_installed(context)


def run(
    inventory: Path | None = None,
    node: str | None = None,
    seed: Path | None = None,
    export: Path | None = None,
) -> Context:
    context = load(inventory, node, seed)
    if seed is not None:
        if export is not None:
            raise ValueError("Join preseeds cannot export peer preseeds")
        if context.is_bootstrap:
            raise ValueError("Use the inventory for bootstrap, not a join preseed")
    else:
        if export is None:
            raise ValueError("Bootstrap requires --node and --export-preseeds")
        if not context.is_bootstrap:
            raise ValueError("An inventory can only bootstrap its first controller")
    # These validations happen before changing machine configuration.
    local_preflight(context)
    common.STATE.mkdir(mode=0o700, parents=True, exist_ok=True)
    common.STATE.chmod(0o700)
    with (common.STATE / "setup.lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if LOCAL_STATE.exists():
            saved = read_state(LOCAL_STATE)
            if (
                saved.deployment != context.deployment
                or saved.local_name != context.local_name
            ):
                raise ValueError(
                    "Existing local state belongs to a different inventory"
                )
            if seed and saved.deployment_id != context.deployment_id:
                raise ValueError("Join preseed belongs to a different deployment")
            context = saved
        else:
            if context.is_bootstrap:
                with quiet_context(context):
                    values = preseed.generate(context)
                context = dataclasses.replace(context, values=values)
            context.write(LOCAL_STATE)
        with quiet_context(context):
            services.stop_unconfigured()
            # Deterministic local name resolution for RabbitMQ. No SSH is used.
            hosts = Path("/etc/hosts").read_text()
            marker = "# regress-stack deployment\n"
            if marker in hosts:
                hosts = hosts.split(marker, 1)[0]
            hosts += marker + "".join(
                f"{member.address} {member.name}\n"
                for member in context.deployment.nodes
            )
            common.write("/etc/hosts", hosts, mode=0o644)
            if context.is_controller:
                database.setup()
                access.setup()
                messaging.setup()
                coordination.setup()
            if "ceph" not in context.deployment.disabled_modules:
                storage.setup()
            networking.setup()
            for module in profiles.local_modules(context):
                if module.configure is None or common.done(f"service-{module.name}"):
                    continue
                module.configure(module.name)
                common.mark(f"service-{module.name}")
                utils.mark_setup(f"regress_stack.modules.{module.name}")
            # Export only after all deployment resources and shared keys exist.
            if export is not None:
                context.export(export, preseed.contributions(context))
            common.mark("local-setup")
    return context
