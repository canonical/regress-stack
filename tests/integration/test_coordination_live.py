# Copyright 2026 - Canonical Ltd
# SPDX-License-Identifier: GPL-3.0-only

"""Opt-in unprivileged protocol test; does not validate VM or systemd integration.

Run with REGRESS_COORDINATION_SERVER pointing at an archive server binary:
uv run --with 'tooz[redis]==6.0.1' py.test tests/integration/test_coordination_live.py
"""

import dataclasses
import os
import socket
import subprocess
import time
import uuid

import pytest

from regress_stack.core.deployment import Context, Deployment, Node, ProviderNetwork
from regress_stack.multinode import coordination


@pytest.mark.skipif(
    not os.environ.get("REGRESS_COORDINATION_SERVER"),
    reason="explicit archive server binary required",
)
def test_sentinel_election_and_rejoin(tmp_path, monkeypatch):
    from tooz import coordination as tooz
    from redis import Redis
    from redis.sentinel import Sentinel

    executable = os.environ["REGRESS_COORDINATION_SERVER"]
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        server_port = sock.getsockname()[1]
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        sentinel_port = sock.getsockname()[1]
    monkeypatch.setattr(coordination, "PORT", server_port)
    monkeypatch.setattr(coordination, "SENTINEL_PORT", sentinel_port)
    nodes = tuple(Node(f"node{i}", f"192.0.2.{i}", "ens3", "ens4") for i in range(1, 4))
    deployment = Deployment(
        "hyperconverged",
        nodes,
        (),
        "192.0.2.10",
        "192.0.2.0/24",
        ProviderNetwork(
            "198.51.100.0/24", "198.51.100.1", "198.51.100.10", "198.51.100.20"
        ),
    )
    ctx = Context(
        deployment,
        "node1",
        str(uuid.uuid4()),
        {
            "coordination/password": "test_secret",
            "coordination/sentinel-password": "test_secret",
        },
    )
    addresses = [f"127.77.42.{i}" for i in range(1, 4)]
    processes = {}
    configs = {}
    logs = []
    clients = []

    def adapt(value):
        for node, address in zip(nodes, addresses):
            value = value.replace(node.address, address)
        return value

    def start(index, kind):
        log = (tmp_path / f"{index}-{kind}.log").open("a")
        logs.append(log)
        command = [executable, str(configs[index, kind])]
        if kind == "sentinel":
            command.append("--sentinel")
        processes[index, kind] = subprocess.Popen(command, stdout=log, stderr=log)

    def until(function, timeout=60):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                if function():
                    return
            except Exception:
                pass
            time.sleep(0.2)
        raise AssertionError("Native coordination state did not converge")

    try:
        for index, node in enumerate(nodes):
            directory = tmp_path / str(index)
            directory.mkdir()
            server, sentinel = coordination.configuration(
                dataclasses.replace(ctx, local_name=node.name)
            )
            for kind, config in (("server", server), ("sentinel", sentinel)):
                config = (
                    adapt(config)
                    .replace("bind 127.0.0.1 ", "bind ")
                    .replace("supervised systemd", "supervised no")
                    .replace("/var/lib/regress-stack-coordination", str(directory))
                )
                path = directory / f"{kind}.conf"
                path.write_text(config)
                path.chmod(0o600)
                configs[index, kind] = path
                start(index, kind)
        discovery = Sentinel(
            [(ip, sentinel_port) for ip in addresses],
            sentinel_kwargs={"password": "test_secret"},
            password="test_secret",
            socket_timeout=1,
        )
        primary = Redis(
            host=addresses[0],
            port=server_port,
            password="test_secret",
            socket_timeout=1,
        )
        until(lambda: primary.info("replication").get("connected_slaves") == 2)
        until(
            lambda: all(
                Redis(
                    host=ip, port=server_port, password="test_secret", socket_timeout=1
                )
                .info("replication")
                .get("master_link_status")
                == "up"
                for ip in addresses[1:]
            )
        )
        until(
            lambda: all(
                len(
                    Redis(
                        host=ip, port=sentinel_port, password="test_secret"
                    ).sentinel_sentinels(coordination.MASTER)
                )
                == 2
                for ip in addresses
            )
        )
        url = adapt(coordination.connection_url(ctx))
        first = tooz.get_coordinator(url, b"worker-one")
        second = tooz.get_coordinator(url, b"worker-two")
        for client in (first, second):
            client.start(start_heart=True)
            clients.append(client)
        lock1, lock2 = first.get_lock(b"volume-test"), second.get_lock(b"volume-test")
        assert lock1.acquire(blocking=False)
        assert not lock2.acquire(blocking=False)
        lock1.release()
        assert lock2.acquire(blocking=False)
        lock2.release()
        # Kill both processes on the bootstrap identity, leaving two Sentinels.
        for kind in ("server", "sentinel"):
            processes[0, kind].kill()
            processes[0, kind].wait(timeout=5)
        until(
            lambda: discovery.discover_master(coordination.MASTER)[0] in addresses[1:]
        )

        def lock_after_failover():
            lock = first.get_lock(b"after-failover")
            if not lock.acquire(blocking=False):
                return False
            lock.release()
            return True

        until(lock_after_failover)
        for kind in ("server", "sentinel"):
            start(0, kind)
        until(lambda: primary.info("replication").get("role") == "slave")
        until(lambda: primary.info("replication").get("master_link_status") == "up")
        assert discovery.discover_master(coordination.MASTER)[0] in addresses[1:]
    finally:
        for client in clients:
            try:
                client.stop()
            except Exception:
                pass
        for process in processes.values():
            if process.poll() is None:
                process.terminate()
        for process in processes.values():
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        for log in logs:
            log.close()
