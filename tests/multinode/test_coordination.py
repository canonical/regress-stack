# Copyright 2026 - Canonical Ltd
# SPDX-License-Identifier: GPL-3.0-only

from types import SimpleNamespace
from unittest.mock import Mock
from urllib.parse import parse_qs, urlsplit

import pytest

from regress_stack.core.deployment import activate
from regress_stack.multinode import coordination


@pytest.mark.parametrize(
    "available,expected",
    [
        (
            ["valkey-server", "valkey-sentinel", "redis-server", "redis-sentinel"],
            "valkey",
        ),
        (["redis-server", "redis-sentinel"], "redis"),
        (["valkey-server", "redis-server", "redis-sentinel"], "redis"),
    ],
)
def test_archive_selection(monkeypatch, available, expected):
    monkeypatch.setattr(
        coordination.apt,
        "get_cache",
        lambda: {name: SimpleNamespace(candidate=True) for name in available},
    )
    assert coordination.implementation() == expected


def test_no_candidate(monkeypatch):
    monkeypatch.setattr(coordination.apt, "get_cache", lambda: {})
    with pytest.raises(RuntimeError):
        coordination.implementation()


def test_preseed_freezes_implementation(context, monkeypatch):
    context.values["coordination/implementation"] = "redis"
    monkeypatch.setattr(
        coordination,
        "implementation",
        Mock(side_effect=AssertionError("must not reselect")),
    )
    assert coordination.packages(context) == [
        "redis-server",
        "redis-sentinel",
        "redis-tools",
        "python3-redis",
    ]


def test_sentinel_discovery_has_all_nodes_and_auth(context):
    url = urlsplit(coordination.connection_url(context))
    assert url.hostname == "192.0.2.1"
    assert url.password == "test_secret"
    query = parse_qs(url.query)
    assert query["sentinel_fallback"] == ["192.0.2.2:26379", "192.0.2.3:26379"]
    assert query["sentinel_password"] == ["test_secret"]


def test_bootstrap_and_replica_configuration(context, peer):
    bootstrap, sentinel = coordination.configuration(context)
    replica, _ = coordination.configuration(peer)
    assert "replicaof" not in bootstrap
    assert "replicaof 192.0.2.1 6379" in replica
    assert "sentinel monitor regress-stack 192.0.2.1 6379 2" in sentinel
    assert "appendonly yes" in bootstrap
    assert "appendfsync always" in bootstrap
    assert "requirepass" in bootstrap and "requirepass" in sentinel


def test_rerun_preserves_native_failover_state(context, monkeypatch):
    monkeypatch.setattr(coordination.common, "done", lambda _: True)
    run = Mock(side_effect=AssertionError("must not restart or reset"))
    monkeypatch.setattr(coordination.common, "run", run)
    with activate(context):
        coordination.setup()
    run.assert_not_called()


def test_compute_does_not_receive_coordination_credentials(context):
    assert all(
        "compute1" not in value.recipients
        for value in coordination.contribute(context).values()
    )


def test_readiness_requires_live_replicas(context, monkeypatch):
    monkeypatch.setattr(coordination, "master_address", lambda _: "192.0.2.1")
    output = {
        "192.0.2.1": "role:master\nslave0:ip=192.0.2.2,port=6379,state=online\nslave1:ip=192.0.2.3,port=6379,state=online\n",
        "192.0.2.2": "role:slave\nmaster_host:192.0.2.1\nmaster_link_status:up\n",
        "192.0.2.3": "role:slave\nmaster_host:192.0.2.1\nmaster_link_status:up\n",
    }
    monkeypatch.setattr(
        coordination.common, "run", lambda _, args, **kwargs: output[args[2]]
    )
    assert coordination.check_members(context)
    output["192.0.2.1"] = "role:master\nslave0:ip=192.0.2.2,port=6379,state=online\n"
    assert not coordination.check_members(context)
    assert coordination.check_members(context, "node3")


def test_data_directory_is_outside_private_setup_state(context):
    server, sentinel = coordination.configuration(context)
    for config in (server, sentinel):
        directory = next(
            line[4:] for line in config.splitlines() if line.startswith("dir ")
        )
        from pathlib import Path

        assert coordination.common.STATE.parent not in Path(directory).parents


@pytest.mark.parametrize(
    "version,expected",
    [
        ("2.10.0", False),
        ("5.0.0", False),
        ("6.0.0", True),
        ("6.0.1", True),
        ("6.1.0", True),
    ],
)
def test_sentinel_auth_support(monkeypatch, version, expected):
    monkeypatch.setattr(
        coordination.apt, "get_upstream_pkg_version", lambda name: version
    )
    assert coordination.sentinel_auth_supported() is expected


def test_unauthenticated_sentinel_keeps_redis_auth(context, monkeypatch):
    context.values["coordination/sentinel-password"] = ""
    server, sentinel = coordination.configuration(context)
    assert "requirepass test_secret" in server
    assert "masterauth test_secret" in server
    assert "requirepass" not in sentinel
    assert "sentinel auth-pass regress-stack test_secret" in sentinel
    url = urlsplit(coordination.connection_url(context))
    assert url.password == "test_secret"
    assert "sentinel_password" not in parse_qs(url.query)
    monkeypatch.setenv("REDISCLI_AUTH", "inherited")
    monkeypatch.setenv("VALKEYCLI_AUTH", "inherited")
    run = Mock(return_value="192.0.2.1\n6379\n")
    monkeypatch.setattr(coordination.common, "run", run)
    assert coordination.master_address(context) == "192.0.2.1"
    assert "REDISCLI_AUTH" not in run.call_args.kwargs["env"]
    assert "VALKEYCLI_AUTH" not in run.call_args.kwargs["env"]


def test_sentinel_uses_routable_source_address(context):
    # Redis 6.0 uses the first bind address for Sentinel's outbound sockets.
    _, sentinel = coordination.configuration(context)
    assert sentinel.splitlines()[0] == f"bind {context.local.address} 127.0.0.1"


@pytest.mark.parametrize(
    "reply,expected",
    [("OK 3 usable Sentinels\n", True), ("NOQUORUM 1 usable Sentinels\n", False)],
)
def test_sentinel_requires_election_quorum(context, monkeypatch, reply, expected):
    monkeypatch.setattr(coordination.common, "run", Mock(return_value=reply))
    assert coordination.check_quorum(context, context.local.address) is expected
