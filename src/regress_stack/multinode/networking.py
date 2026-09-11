# Copyright 2026 - Canonical Ltd
# SPDX-License-Identifier: GPL-3.0-only

from __future__ import annotations

from pathlib import Path

from regress_stack.core.deployment import Context
from regress_stack.multinode import common


def connections(context: Context, port: int) -> str:
    return ",".join(
        f"tcp:{node.address}:{port}" for node in context.deployment.controllers
    )


def central_options(context: Context) -> str:
    local = context.local.address
    options = [f"--db-{db}-addr={local}" for db in ("nb", "sb")]
    for db in ("nb", "sb"):
        options += [
            f"--db-{db}-cluster-local-addr={local}",
            f"--db-{db}-create-insecure-remote=yes",
        ]
        if not context.bootstrap:
            options += [
                f"--db-{db}-cluster-remote-addr={context.deployment.bootstrap.address}"
            ]
    options += [
        f"--ovn-northd-nb-db={connections(context, 6641)}",
        f"--ovn-northd-sb-db={connections(context, 6642)}",
    ]
    return " ".join(options)


def setup() -> None:
    context = common.context()
    if context.controller and not common.done("ovn-central"):
        common.run("systemctl", ["stop", "ovn-central"])
        # Package installation creates standalone empty DBs. Preserve them for
        # diagnosis and let ovn-ctl create/join a clustered DB in their place.
        for db in ("ovnnb_db.db", "ovnsb_db.db"):
            path = Path("/var/lib/ovn") / db
            if path.exists():
                destination = path.with_suffix(".regress-stack-original")
                if destination.exists():
                    raise RuntimeError(
                        "OVN initial database backup already exists; inspect interrupted setup"
                    )
                path.rename(destination)
        common.write(
            "/etc/default/ovn-central",
            f'OVN_CTL_OPTS="{central_options(context)}"\n',
            mode=0o644,
        )
        common.restart("ovn-central")
        common.mark("ovn-central")
    common.write(
        "/etc/openvswitch/system-id.conf", context.local.name + "\n", mode=0o644
    )
    common.restart("openvswitch-switch")
    common.run(
        "ovs-vsctl",
        [
            "set",
            "Open_vSwitch",
            ".",
            f"external_ids:system-id={context.local.name}",
            f"external_ids:hostname={context.local.name}",
            "external_ids:ovn-encap-type=geneve",
            f"external_ids:ovn-encap-ip={context.local.address}",
            f"external_ids:ovn-remote={connections(context, 6642)}",
            "external_ids:ovn-bridge-mappings=physnet1:br-ex",
            *(
                ["external_ids:ovn-cms-options=enable-chassis-as-gw"]
                if context.controller
                else []
            ),
        ],
    )
    if not context.controller:
        common.run(
            "ovs-vsctl",
            ["remove", "Open_vSwitch", ".", "external_ids", "ovn-cms-options"],
        )
    common.run(
        "ovs-vsctl",
        [
            "--may-exist",
            "add-br",
            "br-ex",
            "--",
            "--may-exist",
            "add-port",
            "br-ex",
            context.local.provider_interface,
        ],
    )
    provider_links()
    common.restart("ovn-host")


def provider_links() -> None:
    context = common.context()
    common.write(
        "/etc/systemd/system/regress-stack-provider.service",
        f"""[Unit]
Description=Activate regress-stack provider links
Requires=openvswitch-switch.service
After=openvswitch-switch.service
PartOf=openvswitch-switch.service

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/usr/sbin/ip link set br-ex up
ExecStart=/usr/sbin/ip link set {context.local.provider_interface} up

[Install]
WantedBy=multi-user.target
""",
        mode=0o644,
    )
    common.run("systemctl", ["daemon-reload"])
    common.restart("regress-stack-provider")


def metadata() -> None:
    context = common.context()
    from regress_stack.modules import neutron, utils, ovn

    utils.cfg_set(
        neutron.METADATA_AGENT_CONF,
        ("DEFAULT", "nova_metadata_host", context.deployment.api_address),
        (
            "DEFAULT",
            "nova_metadata_port",
            "18775" if len(context.deployment.controllers) == 3 else "8775",
        ),
        ("DEFAULT", "metadata_proxy_shared_secret", context.secret("neutron/metadata")),
        ("ovs", "ovsdb_connection", ovn.local_connection()),
        ("ovn", "ovn_sb_connection", ovn.sb_connection()),
    )
    common.restart("neutron-ovn-metadata-agent")
