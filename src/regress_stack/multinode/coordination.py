# Copyright 2026 - Canonical Ltd
# SPDX-License-Identifier: GPL-3.0-only

"""Redis protocol coordination with native Sentinel failover."""

import os
from pathlib import Path
from urllib.parse import urlencode

from regress_stack.core import apt
from regress_stack.core.deployment import Context, Secret
from regress_stack.multinode import common


PORT = 6379
SENTINEL_PORT = 26379
MASTER = "regress-stack"


def implementation() -> str:
    cache = apt.get_cache()
    for name in ("valkey", "redis"):
        names = (f"{name}-server", f"{name}-sentinel")
        if all(package in cache and cache[package].candidate for package in names):
            return name
    raise RuntimeError(
        "Neither Valkey nor Redis server and Sentinel packages are available"
    )


def sentinel_auth_supported() -> bool:
    version = apt.get_upstream_pkg_version("python3-tooz")
    if version is None:
        raise RuntimeError(
            "Tooz must be installed before generating coordination state"
        )
    return apt.apt_pkg.version_compare(version, "6.0.0") >= 0


def packages(context: Context):
    name = context.values.get("coordination/implementation") or implementation()
    if name not in ("valkey", "redis"):
        raise ValueError("Unsupported coordination implementation")
    return [f"{name}-server", f"{name}-sentinel", f"{name}-tools", "python3-redis"]


def contribute(context: Context):
    recipients = frozenset(node.name for node in context.deployment.controllers)
    return {
        key: Secret(context.secret(key), recipients)
        for key in (
            "coordination/implementation",
            "coordination/password",
            "coordination/sentinel-password",
        )
    }


def connection_url(context: Context) -> str:
    nodes = context.deployment.controllers
    password = common.token(context.secret("coordination/password"))
    sentinel_password = context.secret("coordination/sentinel-password")
    query = [
        ("sentinel", MASTER),
        ("socket_timeout", "5"),
        ("socket_connect_timeout", "5"),
    ]
    if sentinel_password:
        query.append(("sentinel_password", common.token(sentinel_password)))
    query += [
        ("sentinel_fallback", f"{node.address}:{SENTINEL_PORT}") for node in nodes[1:]
    ]
    return f"redis://:{password}@{nodes[0].address}:{SENTINEL_PORT}?{urlencode(query)}"


def configuration(context: Context):
    password = common.token(context.secret("coordination/password"))
    sentinel_password = context.secret("coordination/sentinel-password")
    server = [
        f"bind 127.0.0.1 {context.local.address}",
        f"port {PORT}",
        "protected-mode yes",
        "daemonize no",
        "supervised systemd",
        f"requirepass {password}",
        f"masterauth {password}",
        f"replica-announce-ip {context.local.address}",
        "appendonly yes",
        "appendfsync always",
        "maxmemory-policy noeviction",
        "dir /var/lib/regress-stack-coordination",
        'logfile ""',
    ]
    if not context.bootstrap:
        server.append(f"replicaof {context.deployment.bootstrap.address} {PORT}")
    quorum = 2 if len(context.deployment.controllers) == 3 else 1
    sentinel = [
        # Redis 6.0 binds outgoing Sentinel sockets to the first address.
        f"bind {context.local.address} 127.0.0.1",
        f"port {SENTINEL_PORT}",
        "protected-mode yes",
        "daemonize no",
        "supervised systemd",
        f"sentinel monitor {MASTER} {context.deployment.bootstrap.address} {PORT} {quorum}",
        f"sentinel auth-pass {MASTER} {password}",
        f"sentinel announce-ip {context.local.address}",
        f"sentinel down-after-milliseconds {MASTER} 5000",
        f"sentinel failover-timeout {MASTER} 30000",
        f"sentinel parallel-syncs {MASTER} 1",
        "dir /var/lib/regress-stack-coordination",
        'logfile ""',
    ]
    if sentinel_password:
        sentinel.append(f"requirepass {common.token(sentinel_password)}")
    return "\n".join(server) + "\n", "\n".join(sentinel) + "\n"


def setup():
    context = common.context()
    if common.done("coordination"):
        # Sentinel rewrites the configuration after election. Do not reinstate
        # the original bootstrap primary on reruns or recovery.
        return
    name = context.secret("coordination/implementation")
    packages(context)  # validate the implementation before using paths
    common.run("systemctl", ["stop", f"{name}-server", f"{name}-sentinel"])
    directory = Path("/var/lib/regress-stack-coordination")
    directory.mkdir(parents=True, exist_ok=True)
    import shutil

    shutil.chown(directory, user=name)
    directory.chmod(0o700)
    server, sentinel = configuration(context)
    # Distribution units allow these configuration paths to be rewritten by
    # Sentinel; preserve package units and their confinement configuration.
    configuration_directory = Path(f"/etc/{name}")
    shutil.chown(configuration_directory, user=name, group=name)
    configuration_directory.chmod(0o750)
    common.write(f"/etc/{name}/{name}.conf", server, user=name)
    common.write(f"/etc/{name}/sentinel.conf", sentinel, user=name)
    # Both package units allow their own data directory, not an arbitrary path.
    # Use a systemd override for the explicitly managed coordination directory.
    for unit in (f"{name}-server", f"{name}-sentinel"):
        common.write(
            f"/etc/systemd/system/{unit}.service.d/regress-stack.conf",
            "[Service]\nReadWritePaths=/var/lib/regress-stack-coordination\n",
            mode=0o644,
        )
    common.run("systemctl", ["daemon-reload"])
    common.restart(f"{name}-server", f"{name}-sentinel")
    common.mark("coordination")


def _sentinel_command(context: Context, command: str, host=None) -> str:
    name = context.secret("coordination/implementation")
    packages(context)
    env = dict(os.environ)
    for key in ("REDISCLI_AUTH", "VALKEYCLI_AUTH"):
        env.pop(key, None)
        if context.secret("coordination/sentinel-password"):
            env[key] = context.secret("coordination/sentinel-password")
    return common.run(
        f"{name}-cli",
        [
            "--raw",
            "-h",
            host or context.local.address,
            "-p",
            str(SENTINEL_PORT),
            "SENTINEL",
            command,
            MASTER,
        ],
        env=env,
    )


def check_quorum(context: Context, host=None) -> bool:
    return _sentinel_command(context, "ckquorum", host).startswith("OK ")


def master_address(context: Context, host=None) -> str:
    output = _sentinel_command(context, "get-master-addr-by-name", host)
    values = output.splitlines()
    if (
        len(values) != 2
        or values[0] not in {node.address for node in context.deployment.controllers}
        or values[1] != str(PORT)
    ):
        raise RuntimeError("Sentinel did not return an expected deployment primary")
    return values[0]


def check_members(context: Context, unavailable=None) -> bool:
    nodes = [
        node for node in context.deployment.controllers if node.name != unavailable
    ]
    primary = master_address(context)
    expected_replicas = {node.address for node in nodes if node.address != primary}
    name = context.secret("coordination/implementation")
    packages(context)
    for node in nodes:
        output = common.run(
            f"{name}-cli",
            ["--raw", "-h", node.address, "-p", str(PORT), "INFO", "replication"],
            env={
                **os.environ,
                "REDISCLI_AUTH": context.secret("coordination/password"),
                "VALKEYCLI_AUTH": context.secret("coordination/password"),
            },
        )
        info = dict(
            line.split(":", 1)
            for line in output.splitlines()
            if ":" in line and not line.startswith("#")
        )
        if node.address == primary:
            replicas = [
                dict(field.split("=", 1) for field in value.split(","))
                for key, value in info.items()
                if key.startswith("slave") and key[5:].isdigit()
            ]
            if (
                info.get("role") != "master"
                or {
                    replica["ip"]
                    for replica in replicas
                    if replica.get("state") == "online"
                }
                != expected_replicas
            ):
                return False
        elif (
            info.get("role") != "slave"
            or info.get("master_host") != primary
            or info.get("master_link_status") != "up"
        ):
            return False
    return primary in {node.address for node in nodes}
