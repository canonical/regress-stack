# Copyright 2026 - Canonical Ltd
# SPDX-License-Identifier: GPL-3.0-only

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
import json
import logging
from typing import TYPE_CHECKING, Any, Protocol, TypedDict

from regress_stack.core.deployment import Context, Node
from regress_stack.modules import keystone
from regress_stack.multinode import common, coordination, networking

if TYPE_CHECKING:
    from openstack.connection import Connection

LOG = logging.getLogger(__name__)


class QueueInfo(TypedDict):
    """Fields used from RabbitMQ's list_queues JSON response."""

    type: str
    durable: bool
    arguments: dict[str, object] | Sequence[Sequence[object]]
    members: list[str] | str
    online: list[str] | str


class _VolumeService(TypedDict):
    host: str
    state: str
    status: str


class _PlacementGroupState(TypedDict):
    state_name: str


class _PlacementGroupMap(TypedDict, total=False):
    pgs_by_state: list[_PlacementGroupState]


class _CephStatus(TypedDict):
    pgmap: _PlacementGroupMap


class _OVNDatabase(TypedDict):
    connected: bool
    model: str


class _OVNQueryResult(TypedDict):
    rows: list[_OVNDatabase]


def queue_replicas_ready(rows: Sequence[QueueInfo], controller_count: int) -> bool:
    def expires(row: QueueInfo) -> bool:
        arguments = row["arguments"]
        # RabbitMQ validates x-expires as an integer; other arguments may have
        # arbitrary AMQP field values.
        if isinstance(arguments, dict):
            value = arguments.get("x-expires", 0)
        else:
            # RabbitMQ 3.12's CLI serializes AMQP field tables as typed triples.
            value = next(
                (value for key, _, value in arguments if key == "x-expires"), 0
            )
        return isinstance(value, int) and value > 0

    quorum = controller_count // 2 + 1
    durable = [row for row in rows if row["durable"]]
    return (
        bool(durable)
        and all(
            row["type"] == "quorum"
            and len(row["members"]) == controller_count
            and len(row["online"]) >= quorum
            for row in durable
        )
        and all(
            row["type"] == "classic" and expires(row)
            for row in rows
            if not row["durable"]
        )
    )


class _Response(Protocol):
    def raise_for_status(self) -> None: ...

    def json(self) -> Any: ...


class _RestProxy(Protocol):
    """The REST call used from either block storage API version's proxy."""

    def get(self, url: str, **kwargs: Any) -> _Response: ...


def volume_hosts(proxy: _RestProxy) -> set[str]:
    # Older OpenStack SDKs lack the services resource wrapper.
    response = proxy.get("/os-services", params={"binary": "cinder-volume"})
    response.raise_for_status()
    services: list[_VolumeService] = response.json()["services"]
    return {
        row["host"].split("@", 1)[0]
        for row in services
        if row["state"] == "up" and row["status"] == "enabled"
    }


def check(context: Context, unavailable: str | None = None) -> list[str]:
    """Read live state. This does not inject failures or create workloads."""
    if not context.is_controller:
        raise ValueError("Run deployment readiness on a controller")
    if unavailable:
        if unavailable not in {node.name for node in context.deployment.nodes}:
            raise ValueError("The unavailable node is not in the deployment")
        if unavailable == context.local.name:
            raise ValueError("Run readiness on a surviving node")
        if not context.deployment.highly_available:
            raise ValueError("Degraded readiness requires three controllers")
    expected_controllers = {
        node.name for node in context.deployment.controllers if node.name != unavailable
    }
    expected_chassis = {
        node.name for node in context.deployment.nodes if node.name != unavailable
    }
    expected_computes = {
        node.name
        for node in context.deployment.compute_nodes
        if node.name != unavailable
    }
    failures: list[str] = []

    def verify(name: str, function: Callable[[], bool]) -> None:
        try:
            if not function():
                failures.append(name)
        except Exception as error:
            # Authentication and service exceptions can contain credentials;
            # record only the exception type so parsing bugs stay visible.
            LOG.warning("%s check raised %s", name, type(error).__name__)
            failures.append(name)

    verify("local setup", lambda: common.done("local-setup"))
    if context.deployment.highly_available:
        expected_addresses = {
            node.address
            for node in context.deployment.controllers
            if node.name != unavailable
        }
        verify(
            "MySQL membership",
            lambda: set(
                common.sql(
                    "SELECT MEMBER_HOST FROM performance_schema.replication_group_members WHERE MEMBER_STATE='ONLINE';"
                ).splitlines()
            )
            == expected_addresses,
        )
    else:
        verify(
            "MySQL writable",
            lambda: common.sql("SELECT @@global.read_only;").strip() == "0",
        )
    verify(
        "RabbitMQ membership",
        lambda: set(
            json.loads(
                common.run("rabbitmqctl", ["cluster_status", "--formatter", "json"])
            )["running_nodes"]
        )
        == {f"rabbit@{name}" for name in expected_controllers},
    )

    def queues() -> bool:
        rows: list[QueueInfo] = json.loads(
            common.run(
                "rabbitmqctl",
                [
                    "list_queues",
                    # RabbitMQ 3.9 probes each replica for the online field;
                    # a down node can exceed the default 60-second deadline.
                    "--timeout",
                    "120",
                    "--vhost",
                    "openstack",
                    "name",
                    "type",
                    "durable",
                    "arguments",
                    "members",
                    "online",
                    "--formatter",
                    "json",
                ],
            )
        )
        # Caracal uses classic, expiring reply/fanout queues. Durable service
        # queues must have all planned members and a live majority.
        return queue_replicas_ready(rows, len(context.deployment.controllers))

    verify("RabbitMQ queue replicas", queues)
    if "ceph" not in context.deployment.disabled_modules:
        verify(
            "Ceph monitor quorum",
            lambda: set(
                json.loads(common.run("ceph", ["quorum_status", "--format", "json"]))[
                    "quorum_names"
                ]
            )
            == expected_controllers,
        )

    def placement_groups() -> bool:
        status: _CephStatus = json.loads(
            common.run("ceph", ["status", "--format", "json"])
        )
        pgs = status["pgmap"].get("pgs_by_state", [])
        allowed = (
            {"active", "clean"}
            if not unavailable
            else {
                "active",
                "clean",
                "undersized",
                "degraded",
                "remapped",
                "recovering",
                "recovery_wait",
                "backfilling",
                "backfill_wait",
            }
        )
        return bool(pgs) and all(
            "active" in pg["state_name"].split("+")
            and set(pg["state_name"].split("+")) <= allowed
            for pg in pgs
        )

    if "ceph" not in context.deployment.disabled_modules:
        verify("Ceph usable placement groups", placement_groups)
    for node in context.deployment.controllers:
        if node.name == unavailable:
            continue
        for port, database in ((6641, "OVN_Northbound"), (6642, "OVN_Southbound")):

            def connected(
                node: Node = node, port: int = port, database: str = database
            ) -> bool:
                result: list[_OVNQueryResult] = json.loads(
                    common.run(
                        "ovsdb-client",
                        [
                            "query",
                            f"tcp:{node.address}:{port}",
                            json.dumps(
                                [
                                    "_Server",
                                    {
                                        "op": "select",
                                        "table": "Database",
                                        "where": [["name", "==", database]],
                                        "columns": ["connected", "model"],
                                    },
                                ]
                            ),
                        ],
                    )
                )
                rows = result[0]["rows"]
                return (
                    len(rows) == 1
                    and rows[0]["connected"]
                    and rows[0]["model"] == "clustered"
                )

            verify(f"OVN {database} on {node.name}", connected)

    def chassis() -> bool:
        rows: list[list[str]] = json.loads(
            common.run(
                "ovn-sbctl",
                [
                    f"--db={networking.connections(context, 6642)}",
                    "--format=json",
                    "--columns=name,hostname",
                    "list",
                    "Chassis",
                ],
            )
        )["data"]
        return {
            name for name, hostname in rows if name == hostname and name != unavailable
        } == expected_chassis

    verify("OVN compute host identities", chassis)
    live_controllers = [
        node for node in context.deployment.controllers if node.name != unavailable
    ]
    for node in live_controllers:

        def sentinel(node: Node = node) -> bool:
            return coordination.check_quorum(context, node.address)

        verify(f"Sentinel on {node.name}", sentinel)

    def sentinel_agreement() -> bool:
        masters = {
            coordination.master_address(context, node.address)
            for node in live_controllers
        }
        return len(masters) == 1 and masters <= {
            node.address for node in live_controllers
        }

    verify("Sentinel primary agreement", sentinel_agreement)

    verify(
        "Redis/Valkey replication",
        lambda: coordination.check_members(context, unavailable),
    )

    try:
        connection: Connection = keystone.o7k()
    except Exception:
        failures.append("OpenStack client authentication")
        return failures

    def authenticates() -> bool:
        # The SDK's authorize() returns the token but lacks annotations.
        authorize: Callable[[], str] = connection.authorize
        return bool(authorize())

    verify("Keystone authentication", authenticates)

    def computes() -> bool:
        rows = list(connection.compute.services(binary="nova-compute"))
        return {
            row.host for row in rows if row.state == "up" and row.status == "enabled"
        } == expected_computes

    verify("Nova compute registration", computes)

    if "cinder" not in context.deployment.disabled_modules:
        verify(
            "Cinder volume registration",
            lambda: volume_hosts(connection.block_storage) == expected_controllers,
        )
    api_queries: tuple[tuple[str, Callable[[], Iterable[object]]], ...] = (
        ("compute API", lambda: connection.compute.flavors()),
        ("image API", lambda: connection.image.images()),
        ("network API", lambda: connection.network.networks()),
    )
    if "cinder" not in context.deployment.disabled_modules:
        api_queries += (("volume API", lambda: connection.block_storage.volumes()),)
    for name, query in api_queries:

        def responds(query: Callable[[], Iterable[object]] = query) -> bool:
            list(query())
            return True

        verify(name, responds)
    return failures
