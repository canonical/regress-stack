# Copyright 2026 - Canonical Ltd
# SPDX-License-Identifier: GPL-3.0-only

from __future__ import annotations

from collections.abc import Callable, Iterator
import contextlib
import fcntl
import functools
import json
import logging
import os
from pathlib import Path
import socket
from typing import TYPE_CHECKING, TypedDict
import uuid

from regress_stack.core import profiles, utils
from regress_stack.core.deployment import Context, Deployment, activate
from regress_stack.multinode import (
    access,
    common,
    coordination,
    database,
    messaging,
    networking,
    preseed,
    services,
    storage,
)

if TYPE_CHECKING:
    from typing import ParamSpec, TypeVar

    P = ParamSpec("P")
    R = TypeVar("R")


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
    levels = {
        name: logging.getLogger(name).level
        for name in ("", "openstack", "keystoneauth", "urllib3")
    }
    for name in levels:
        logging.getLogger(name).setLevel(logging.WARNING)
    from regress_stack.modules import keystone

    keystone.o7k.cache_clear()
    try:
        with activate(context, bootstrap_only=bootstrap_only):
            yield
    finally:
        keystone.o7k.cache_clear()
        for name, level in levels.items():
            logging.getLogger(name).setLevel(level)


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
    profiles.execution_order(context)


def run(
    inventory: Path | None = None,
    node: str | None = None,
    seed: Path | None = None,
    export: Path | None = None,
) -> Context:
    if (inventory is None) == (seed is None):
        raise ValueError("Supply either --inventory and --node, or --preseed")
    if seed:
        if node or export:
            raise ValueError(
                "Join preseeds already identify the node and cannot export peers"
            )
        context = Context.read(seed)
        if context.bootstrap:
            raise ValueError("Use the inventory for bootstrap, not a join preseed")
    else:
        if not node or not export:
            raise ValueError("Bootstrap requires --node and --export-preseeds")
        assert inventory is not None  # Validated exclusive inventory/preseed above.
        deployment = Deployment.read(inventory)
        context = Context(deployment, node, str(uuid.uuid4()))
        if not context.bootstrap:
            raise ValueError("An inventory can only bootstrap its first controller")
    # These validations happen before changing machine configuration.
    local_preflight(context)
    common.STATE.mkdir(mode=0o700, parents=True, exist_ok=True)
    common.STATE.chmod(0o700)
    with (common.STATE / "setup.lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if LOCAL_STATE.exists():
            saved = Context.read(LOCAL_STATE)
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
        elif context.bootstrap:
            with quiet_context(context):
                preseed.generate(context)
            preseed.save(context, LOCAL_STATE)
        else:
            preseed.save(context, LOCAL_STATE)
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
            if context.controller:
                database.setup()
                access.setup()
                messaging.setup()
                coordination.setup()
            storage.setup()
            networking.setup()
            for module in profiles.execution_order(context, check_packages=False):
                if module.name in {"utils", "mysql", "rabbitmq", "ceph", "ovn"}:
                    continue
                if common.done(f"service-{module.name}"):
                    continue
                if module.name == "neutron" and not context.controller:
                    networking.metadata()
                else:
                    services.prepare(module.name)
                    module.module.setup()
                    services.finish(module.name)
                common.mark(f"service-{module.name}")
                utils.mark_setup(f"regress_stack.modules.{module.name}")
            # Export only after all deployment resources and shared keys exist.
            if context.bootstrap:
                assert export is not None  # Required for bootstrap above.
                context.export(export, preseed.contributions(context))
            common.mark("local-setup")
    return context


def with_local_context(function: Callable[P, R]) -> Callable[P, R]:
    """Use the saved deployment credentials for the existing Tempest command."""

    @functools.wraps(function)
    def wrapped(*args: P.args, **kwargs: P.kwargs) -> R:
        if not LOCAL_STATE.exists():
            return function(*args, **kwargs)
        context = Context.read(LOCAL_STATE)
        if not context.controller:
            raise RuntimeError("Run Tempest from a controller")
        with quiet_context(context, bootstrap_only=False):
            return function(*args, **kwargs)

    return wrapped
