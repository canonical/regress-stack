# Copyright 2026 - Canonical Ltd
# SPDX-License-Identifier: GPL-3.0-only

import dataclasses
import uuid

import pytest

from regress_stack.core.deployment import Context, Deployment, Node, ProviderNetwork


@pytest.fixture
def context():
    deployment = Deployment(
        "hyperconverged",
        tuple(Node(f"node{i}", f"192.0.2.{i}", "ens3", "ens4") for i in range(1, 4)),
        (Node("compute1", "192.0.2.4", "ens3", "ens4"),),
        "192.0.2.10",
        "192.0.2.0/24",
        ProviderNetwork(
            "198.51.100.0/24", "198.51.100.1", "198.51.100.20", "198.51.100.80"
        ),
    )
    return Context(
        deployment,
        "node1",
        str(uuid.uuid4()),
        {
            "coordination/implementation": "valkey",
            "coordination/password": "test_secret",
            "coordination/sentinel-password": "test_secret",
            "mysql/recovery": "recovery_secret",
            "mysql/check": "check_secret",
            "ceph/fsid": str(uuid.uuid4()),
            "rabbitmq/cookie": "cookie_secret",
            "mysql/nova": "db_secret",
            "cinder/service-type": "volumev3",
            "rabbitmq/nova": "rabbit_secret",
            "keystone/nova": "identity_secret",
            "keystone/admin": "admin_secret",
            "ceph/rbd_uuid": str(uuid.uuid4()),
            "ceph/client.volumes": "volume_secret",
            "neutron/metadata": "metadata_secret",
            "ceph/mgr.node1": "manager1_secret",
            "ceph/mgr.node2": "manager2_secret",
            "ceph/mgr.node3": "manager3_secret",
        },
    )


@pytest.fixture
def peer(context):
    return dataclasses.replace(context, local_name="node2")


@pytest.fixture
def compute(context):
    return dataclasses.replace(context, local_name="compute1")
