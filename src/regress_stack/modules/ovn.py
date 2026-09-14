# Copyright 2025 - Canonical Ltd
# SPDX-License-Identifier: GPL-3.0-only

import ipaddress
import logging
import pathlib
import re
import subprocess

import pyroute2

from regress_stack.core import utils as core_utils

LOG = logging.getLogger(__name__)

LOGS = ["/var/log/ovn/"]
PACKAGES = [
    "ovn-central",
    "openvswitch-switch",
    "ovn-host",
]

EXTERNAL_BRIDGE = "br-ex"
EXTERNAL_CIDR = "10.127.147.0/24"

OVN_ENCAP_IP = core_utils.my_ip()
# OVSDB_CONNECTION = "unix:/var/run/openvswitch/db.sock"
OVSDB_CONNECTION = f"tcp:{core_utils.my_ip()}:6640"
OVNNB_CONNECTION = f"tcp:{core_utils.my_ip()}:6641"
OVNSB_CONNECTION = f"tcp:{core_utils.my_ip()}:6642"

SYSTEM_ID = "/etc/openvswitch/system-id.conf"

OVS_CTL_OPTS = f"--ovsdb-server-options='--remote=ptcp:6640:{core_utils.my_ip()}'"

OVN_CTL_OPTS = f"""--db-nb-addr={core_utils.my_ip()} \
  --db-sb-addr={core_utils.my_ip()} \
  --db-nb-cluster-local-addr={core_utils.my_ip()} \
  --db-sb-cluster-local-addr={core_utils.my_ip()} \
  --db-nb-create-insecure-remote=yes \
  --db-sb-create-insecure-remote=yes \
  --ovn-northd-nb-db={OVNNB_CONNECTION} \
  --ovn-northd-sb-db={OVNSB_CONNECTION} \
"""


def setup():
    system_id = core_utils.fqdn()
    pathlib.Path(SYSTEM_ID).write_text(system_id)
    pathlib.Path("/etc/default/openvswitch-switch").write_text(
        f"OVS_CTL_OPTS={OVS_CTL_OPTS}"
    )
    pathlib.Path("/etc/default/ovn-central").write_text(f"OVN_CTL_OPTS={OVN_CTL_OPTS}")
    core_utils.restart_service("ovn-central")
    core_utils.restart_service("openvswitch-switch")
    core_utils.run(
        "ovs-vsctl",
        [
            "--retry",
            "set",
            "open",
            ".",
            "external_ids:ovn-encap-type=geneve",
            "--",
            "set",
            "open",
            ".",
            f"external_ids:ovn-encap-ip={OVN_ENCAP_IP}",
            "--",
            "set",
            "open",
            ".",
            f"external_ids:system-id={system_id}",
            "--",
            "set",
            "open",
            ".",
            "external_ids:ovn-match-northd-version=true",
            "--",
            "set",
            "open",
            ".",
            f"external_ids:ovn-remote={OVNSB_CONNECTION}",
        ],
    )
    core_utils.run(
        "ovs-vsctl",
        [
            "--retry",
            "--may-exist",
            "add-br",
            EXTERNAL_BRIDGE,
            "--",
            "set",
            "bridge",
            EXTERNAL_BRIDGE,
            "datapath_type=system",
            "protocols=OpenFlow13,OpenFlow15",
        ],
    )
    core_utils.run(
        "ovs-vsctl",
        [
            "--retry",
            "set",
            "open",
            ".",
            f"external_ids:ovn-bridge-mappings=physnet1:{EXTERNAL_BRIDGE}",
            "--",
            "set",
            "open",
            ".",
            "external_ids:ovn-cms-options=enable-chassis-as-gw",
        ],
    )
    configure_external_bridge()
    _add_iptable_postrouting_rule(EXTERNAL_CIDR, "ovn-external-bridge")


def configure_external_bridge():
    network = ipaddress.ip_network(EXTERNAL_CIDR)
    ip = str(next(network.hosts()))
    gw_ip = ip + "/" + str(network.prefixlen)
    with pyroute2.NDB() as ndb:
        with ndb.interfaces[EXTERNAL_BRIDGE] as iface:
            try:
                iface.ipaddr[gw_ip]
            except KeyError:
                iface.add_ip(gw_ip)
                iface.set(state="up")


def _add_iptable_postrouting_rule(cidr: str, comment: str) -> None:
    """Add postrouting iptable rule.

    Add new postiprouting iptable rule, if it does not exist, to allow traffic
    for cidr network.
    """
    executable = "iptables-legacy"
    rule_def = [
        "POSTROUTING",
        "-w",
        "-t",
        "nat",
        "-s",
        cidr,
        "-j",
        "MASQUERADE",
        "-m",
        "comment",
        "--comment",
        comment,
    ]
    found = False
    try:
        core_utils.run(executable, ["--check", *rule_def])
    except subprocess.CalledProcessError as e:
        # --check has an RC of 1 if the rule does not exist
        if e.returncode == 1 and re.search(r"No.*match by that name", e.stderr):
            LOG.debug(f"Postrouting iptable rule for {cidr} missing")
            found = False
        else:
            LOG.warning(f"Failed to lookup postrouting iptable rule for {cidr}")
    else:
        # If not exception was raised then the rule exists.
        LOG.debug(f"Found existing postrouting rule for {cidr}")
        found = True
    if not found:
        LOG.debug(f"Adding postrouting iptable rule for {cidr}")
        core_utils.run(executable, ["--append", *rule_def])


def local_connection() -> str:
    from regress_stack.core.deployment import current

    return "unix:/var/run/openvswitch/db.sock" if current() else OVSDB_CONNECTION


def nb_connection() -> str:
    from regress_stack.core.deployment import current
    from regress_stack.multinode.networking import connections

    return connections(current(), 6641) if current() else OVNNB_CONNECTION


def sb_connection() -> str:
    from regress_stack.core.deployment import current
    from regress_stack.multinode.networking import connections

    return connections(current(), 6642) if current() else OVNSB_CONNECTION
