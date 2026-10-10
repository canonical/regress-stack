# Copyright 2026 - Canonical Ltd
# SPDX-License-Identifier: GPL-3.0-only

"""Where single-node service setup and explicit deployments differ.

Service modules were written for one machine. In an explicit deployment the
same setup code runs on every node, against a shared database, message bus and
API address. Service modules ask the questions below by name instead of
reading the deployment context.

This lives outside regress_stack.modules, which is scanned for service modules.
It imports only multinode modules that do not import regress_stack.modules, so
it introduces no import cycle.
"""

from __future__ import annotations

from collections.abc import Sequence

from regress_stack.core import apt as core_apt
from regress_stack.core import deployment
from regress_stack.core import utils as core_utils
from regress_stack.core.deployment import Context, Profile, ProviderNetwork
from regress_stack.multinode import database, messaging, networking

# Package-default Open vSwitch listener used by legacy setup.
_LEGACY_OVSDB_PORT = 6640
_LOCAL_OVSDB_SOCKET = "unix:/var/run/openvswitch/db.sock"


def context() -> Context | None:
    return deployment.current()


def explicit() -> bool:
    return context() is not None


def is_bootstrap_node() -> bool:
    """Whether this node creates the deployment's shared resources."""
    ctx = context()
    return ctx is None or ctx.is_bootstrap


def owns_shared_state() -> bool:
    """Whether the current phase may create or migrate shared resources.

    Setup confines this to the bootstrap node. Runtime phases, such as
    Tempest, may run on any surviving controller.
    """
    return is_bootstrap_node() or not deployment.bootstrap_only()


def shared_sudo(cmd: str, args: Sequence[str], user: str | None = None) -> None:
    """Run a shared-state command, such as a schema migration, once."""
    if owns_shared_state():
        core_utils.sudo(cmd, args, user=user)


def secret(key: str, default: str) -> str:
    """A preseeded deployment secret, or the fixed single-node default."""
    ctx = context()
    return ctx.secret(key) if ctx else default


def deployment_secret(key: str) -> str:
    """A preseeded secret that only exists in an explicit deployment."""
    ctx = context()
    if ctx is None:
        raise RuntimeError(f"{key} is only defined for explicit deployments")
    return ctx.secret(key)


def preseeded(key: str) -> str | None:
    ctx = context()
    return ctx.values.get(key) if ctx else None


def installed(name: str, packages: Sequence[str]) -> bool:
    """Explicit profiles fix their modules; single-node setup discovers them."""
    ctx = context()
    if ctx is not None:
        return name not in ctx.deployment.disabled_modules
    return core_apt.pkgs_installed(list(packages))


def images_on_ceph() -> bool:
    """Explicit deployments share images through Ceph unless it is disabled."""
    ctx = context()
    return ctx is not None and "ceph" not in ctx.deployment.disabled_modules


def is_controller() -> bool:
    ctx = context()
    return ctx is None or ctx.is_controller


def runs_compute() -> bool:
    ctx = context()
    return ctx is None or ctx.runs_compute


def _control_profile() -> bool:
    ctx = context()
    return ctx is not None and ctx.deployment.profile is Profile.CONTROL


def spice_console_enabled() -> bool:
    return not _control_profile()


def distributed_floating_ips() -> bool:
    return not _control_profile()


def service_url(port: int, path: str = "") -> str:
    """The catalog URL of an API listening on the given controller port."""
    ctx = context()
    if ctx is None:
        return f"http://{core_utils.my_ip()}:{port}{path}"
    published = ctx.deployment.api_port(port)
    return f"http://{ctx.deployment.api_address}:{published}{path}"


def setup_client_overrides() -> dict[str, str]:
    """Pin setup-time API clients on a controller to its own services.

    Planned peers can have package-default API listeners before joining.
    Setup must consume the services it has configured on this machine.
    """
    ctx = context()
    if ctx is None or not ctx.is_controller or not deployment.bootstrap_only():
        return {}
    local = ctx.local.address
    return {
        "auth_url": f"http://{local}:5000/v3/",
        "identity_endpoint_override": f"http://{local}:5000/v3/",
        "network_endpoint_override": f"http://{local}:9696/v2.0",
    }


def database_host() -> str:
    ctx = context()
    if ctx is None:
        return "localhost"
    if ctx.deployment.profile is Profile.CONTROL:
        return f"{ctx.deployment.api_address}:3306"
    # The local HAProxy routes to the current Group Replication primary.
    return "127.0.0.1:13306"


def database_account(name: str) -> tuple[str, str]:
    return database.ensure_service(name)


def messaging_hosts() -> list[str]:
    ctx = context()
    if ctx is None:
        return ["localhost"]
    return [node.address for node in ctx.deployment.controllers]


def messaging_account(name: str) -> tuple[str, str]:
    return messaging.ensure_service(name)


def ovn_database(port: int) -> str:
    ctx = context()
    if ctx is None:
        return f"tcp:{core_utils.my_ip()}:{port}"
    return networking.connections(ctx, port)


def ovsdb_connection() -> str:
    if explicit():
        return _LOCAL_OVSDB_SOCKET
    return f"tcp:{core_utils.my_ip()}:{_LEGACY_OVSDB_PORT}"


def node_name() -> str | None:
    ctx = context()
    return ctx.local.name if ctx else None


def provider_network() -> ProviderNetwork | None:
    ctx = context()
    return ctx.deployment.provider if ctx else None
