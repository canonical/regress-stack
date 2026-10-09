# Copyright 2026 - Canonical Ltd
# SPDX-License-Identifier: GPL-3.0-only

from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator, Sequence
import dataclasses
import functools
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


_DEGRADED_PG_STATES = frozenset(
    {
        "undersized",
        "degraded",
        "remapped",
        "recovering",
        "recovery_wait",
        "backfilling",
        "backfill_wait",
    }
)

Probe = Callable[[], bool]


@dataclasses.dataclass(frozen=True)
class _Expected:
    """Live state expected while the optional unavailable node is stopped."""

    context: Context
    unavailable: str | None

    def _live(self, nodes: Iterable[Node]) -> list[Node]:
        return [node for node in nodes if node.name != self.unavailable]

    @property
    def controllers(self) -> list[Node]:
        return self._live(self.context.deployment.controllers)

    @property
    def controller_names(self) -> set[str]:
        return {node.name for node in self.controllers}

    @property
    def chassis(self) -> set[str]:
        return {node.name for node in self._live(self.context.deployment.nodes)}

    @property
    def computes(self) -> set[str]:
        return {node.name for node in self._live(self.context.deployment.compute_nodes)}

    def enabled(self, module: str) -> bool:
        return module not in self.context.deployment.disabled_modules


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
    expected = _Expected(context, unavailable)
    failures = [
        name for name, probe in _service_checks(expected) if not _passes(name, probe)
    ]
    try:
        connection: Connection = keystone.o7k()
    except Exception:
        failures.append("OpenStack client authentication")
        return failures
    failures += [
        name
        for name, probe in _api_checks(expected, connection)
        if not _passes(name, probe)
    ]
    return failures


def _passes(name: str, probe: Probe) -> bool:
    try:
        return probe()
    except Exception as error:
        # Authentication and service exceptions can contain credentials;
        # record only the exception type so parsing bugs stay visible.
        LOG.warning("%s check raised %s", name, type(error).__name__)
        return False


def _service_checks(expected: _Expected) -> Iterator[tuple[str, Probe]]:
    context = expected.context
    yield "local setup", functools.partial(common.done, "local-setup")
    if context.deployment.highly_available:
        yield "MySQL membership", functools.partial(_mysql_members, expected)
    else:
        yield "MySQL writable", _mysql_writable
    yield "RabbitMQ membership", functools.partial(_rabbitmq_members, expected)
    yield "RabbitMQ queue replicas", functools.partial(_rabbitmq_queues, context)
    if expected.enabled("ceph"):
        yield "Ceph monitor quorum", functools.partial(_ceph_quorum, expected)
        yield (
            "Ceph usable placement groups",
            functools.partial(
                _ceph_placement_groups, degraded=expected.unavailable is not None
            ),
        )
    for node in expected.controllers:
        for port, database in ((6641, "OVN_Northbound"), (6642, "OVN_Southbound")):
            yield (
                f"OVN {database} on {node.name}",
                functools.partial(_ovn_clustered, node, port, database),
            )
    yield "OVN compute host identities", functools.partial(_ovn_chassis, expected)
    for node in expected.controllers:
        yield (
            f"Sentinel on {node.name}",
            functools.partial(coordination.check_quorum, context, node.address),
        )
    yield "Sentinel primary agreement", functools.partial(_sentinel_agreement, expected)
    yield (
        "Redis/Valkey replication",
        functools.partial(coordination.check_members, context, expected.unavailable),
    )


def _api_checks(
    expected: _Expected, connection: Connection
) -> Iterator[tuple[str, Probe]]:
    # Probes resolve SDK proxies lazily: proxy creation can fail as well.
    yield "Keystone authentication", functools.partial(_authenticates, connection)
    yield (
        "Nova compute registration",
        functools.partial(_compute_hosts_registered, expected, connection),
    )
    if expected.enabled("cinder"):
        yield (
            "Cinder volume registration",
            lambda: volume_hosts(connection.block_storage) == expected.controller_names,
        )
    yield "compute API", lambda: _responds(connection.compute.flavors)
    yield "image API", lambda: _responds(connection.image.images)
    yield "network API", lambda: _responds(connection.network.networks)
    if expected.enabled("cinder"):
        yield "volume API", lambda: _responds(connection.block_storage.volumes)


def _mysql_members(expected: _Expected) -> bool:
    members = common.sql(
        "SELECT MEMBER_HOST FROM performance_schema.replication_group_members"
        " WHERE MEMBER_STATE='ONLINE';"
    ).splitlines()
    return set(members) == {node.address for node in expected.controllers}


def _mysql_writable() -> bool:
    return common.sql("SELECT @@global.read_only;").strip() == "0"


def _rabbitmq_members(expected: _Expected) -> bool:
    status = json.loads(
        common.run("rabbitmqctl", ["cluster_status", "--formatter", "json"])
    )
    return set(status["running_nodes"]) == {
        f"rabbit@{name}" for name in expected.controller_names
    }


def _rabbitmq_queues(context: Context) -> bool:
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


def _ceph_quorum(expected: _Expected) -> bool:
    status = json.loads(common.run("ceph", ["quorum_status", "--format", "json"]))
    return set(status["quorum_names"]) == expected.controller_names


def _ceph_placement_groups(*, degraded: bool) -> bool:
    status: _CephStatus = json.loads(common.run("ceph", ["status", "--format", "json"]))
    pgs = status["pgmap"].get("pgs_by_state", [])
    allowed = {"active", "clean"} | (_DEGRADED_PG_STATES if degraded else set())
    return bool(pgs) and all(
        "active" in pg["state_name"].split("+")
        and set(pg["state_name"].split("+")) <= allowed
        for pg in pgs
    )


def _ovn_clustered(node: Node, port: int, database: str) -> bool:
    query = [
        "_Server",
        {
            "op": "select",
            "table": "Database",
            "where": [["name", "==", database]],
            "columns": ["connected", "model"],
        },
    ]
    result: list[_OVNQueryResult] = json.loads(
        common.run(
            "ovsdb-client", ["query", f"tcp:{node.address}:{port}", json.dumps(query)]
        )
    )
    rows = result[0]["rows"]
    return len(rows) == 1 and rows[0]["connected"] and rows[0]["model"] == "clustered"


def _ovn_chassis(expected: _Expected) -> bool:
    rows: list[list[str]] = json.loads(
        common.run(
            "ovn-sbctl",
            [
                f"--db={networking.connections(expected.context, 6642)}",
                "--format=json",
                "--columns=name,hostname",
                "list",
                "Chassis",
            ],
        )
    )["data"]
    return {
        name
        for name, hostname in rows
        if name == hostname and name != expected.unavailable
    } == expected.chassis


def _sentinel_agreement(expected: _Expected) -> bool:
    masters = {
        coordination.master_address(expected.context, node.address)
        for node in expected.controllers
    }
    return len(masters) == 1 and masters <= {
        node.address for node in expected.controllers
    }


def _authenticates(connection: Connection) -> bool:
    # The SDK's authorize() returns the token but lacks annotations.
    authorize: Callable[[], str] = connection.authorize
    return bool(authorize())


def _compute_hosts_registered(expected: _Expected, connection: Connection) -> bool:
    rows = list(connection.compute.services(binary="nova-compute"))
    return {
        row.host for row in rows if row.state == "up" and row.status == "enabled"
    } == expected.computes


def _responds(query: Callable[[], Iterable[object]]) -> bool:
    list(query())
    return True
