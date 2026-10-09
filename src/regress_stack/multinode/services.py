# Copyright 2026 - Canonical Ltd
# SPDX-License-Identifier: GPL-3.0-only

from __future__ import annotations

from collections.abc import Callable
import importlib
from pathlib import Path
import re
import shutil

from regress_stack import topology
from regress_stack.core.deployment import Context
from regress_stack.modules import keystone, neutron, ovn, utils
from regress_stack.multinode import common, coordination


CONFIGS = {
    "barbican": "/etc/barbican/barbican.conf",
    "cinder": "/etc/cinder/cinder.conf",
    "heat": "/etc/heat/heat.conf",
    "magnum": "/etc/magnum/magnum.conf",
    "neutron": "/etc/neutron/neutron.conf",
    "nova": "/etc/nova/nova.conf",
    "watcher": "/etc/watcher/watcher.conf",
}


def stop_unconfigured() -> None:
    # Package postinsts start daemons before they have usable configuration.
    # Their restart loops pull RabbitMQ back up while its cookie is replaced.
    units = common.run(
        "systemctl", ["list-unit-files", "--type=service", "--no-legend", "--plain"]
    )
    pending = []
    for line in units.splitlines():
        unit = line.split()[0]
        module = unit.split("-", 1)[0].removesuffix(".service")
        if module in CONFIGS and not common.done(f"service-{module}"):
            pending.append(unit)
    if pending:
        common.run("systemctl", ["stop", *pending])


APACHE_CONFIG_DIRS = (
    Path("/etc/apache2/sites-available"),
    Path("/etc/apache2/conf-available"),
)


def limit_wsgi_workers(name: str) -> None:
    # Service worker options do not control mod_wsgi process counts.
    for directory in APACHE_CONFIG_DIRS:
        for config in directory.glob("*.conf"):
            original = config.read_text()
            lines = []
            for line in original.splitlines(keepends=True):
                fields = line.split()
                if (
                    len(fields) > 2
                    and fields[0] == "WSGIDaemonProcess"
                    and (fields[1] == name or fields[1].startswith(name + "-"))
                ):
                    line = re.sub(r"(?<!\S)processes=\d+(?=\s|$)", "processes=1", line)
                lines.append(line)
            updated = "".join(lines)
            if updated != original:
                config.write_text(updated)


def configure(name: str) -> None:
    """Configure a service module with the multinode options around its setup."""
    prepare(name)
    importlib.import_module(f"regress_stack.modules.{name}").setup()
    finish(name)


def configure_metadata_agent(name: str) -> None:
    """Compute nodes run only the Neutron metadata agent."""
    metadata_agent()


def prepare(name: str) -> None:
    """Set multinode options before the existing module configures and starts."""
    context = common.context()
    if context.is_controller:
        limit_wsgi_workers(name)
    if name in CONFIGS:
        utils.cfg_set(
            CONFIGS[name],
            ("DEFAULT", "host", context.local.name),
            ("oslo_messaging_rabbit", "rabbit_quorum_queue", "true"),
            ("oslo_messaging_rabbit", "amqp_durable_queues", "true"),
        )
    if service_preparation := _PREPARATIONS.get(name):
        service_preparation(context)


def finish(name: str) -> None:
    if name == "neutron":
        metadata_agent()
    elif name == "cinder":
        common.restart("cinder-volume")


def _prepare_keystone(context: Context) -> None:
    common.run("systemctl", ["start", "memcached"])
    for folder in ("fernet-keys", "credential-keys"):
        directory = Path("/etc/keystone") / folder
        directory.mkdir(parents=True, exist_ok=True)
        shutil.chown(directory, user="keystone", group="keystone")
        directory.chmod(0o700)
        for number in ("0", "1"):
            common.write(
                directory / number,
                context.secret(f"keystone/{folder}/{number}"),
                user="keystone",
            )


def _prepare_nova(context: Context) -> None:
    utils.cfg_set(
        CONFIGS["nova"], ("scheduler", "discover_hosts_in_cells_interval", "10")
    )
    if not context.runs_compute:
        return
    common.run("systemctl", ["start", "libvirtd"])
    settings = [
        # Caracal's native os-vif driver opens OVSDB without privsep.
        # Its packaged vsctl driver uses the existing rootwrap helper.
        ("os_vif_ovs", "ovsdb_interface", "vsctl"),
    ]
    if "ceph" not in context.deployment.disabled_modules:
        settings.extend(
            (
                ("libvirt", "images_type", "rbd"),
                ("libvirt", "images_rbd_pool", "volumes"),
                ("libvirt", "images_rbd_ceph_conf", "/etc/ceph/ceph.conf"),
            )
        )
    utils.cfg_set(CONFIGS["nova"], *settings)


def _prepare_cinder(context: Context) -> None:
    utils.cfg_set(
        CONFIGS["cinder"],
        ("DEFAULT", "cluster", "regress-stack"),
        ("coordination", "backend_url", coordination.connection_url(context)),
        ("service_user", "send_service_user_token", "true"),
        *utils.dict_to_cfg_set_args(
            "service_user",
            keystone.account_dict("cinder", context.secret("keystone/cinder")),
        ),
    )


def _prepare_heat(context: Context) -> None:
    utils.cfg_set(
        CONFIGS["heat"],
        ("DEFAULT", "auth_encryption_key", context.secret("heat/auth_encryption_key")),
    )


_PREPARATIONS: dict[str, Callable[[Context], None]] = {
    "keystone": _prepare_keystone,
    "nova": _prepare_nova,
    "cinder": _prepare_cinder,
    "heat": _prepare_heat,
}


def metadata_agent() -> None:
    context = common.context()
    utils.cfg_set(
        neutron.METADATA_AGENT_CONF,
        ("DEFAULT", "nova_metadata_host", context.deployment.api_address),
        (
            "DEFAULT",
            "nova_metadata_port",
            str(context.deployment.api_port(8775)),
        ),
        ("DEFAULT", "metadata_proxy_shared_secret", context.secret("neutron/metadata")),
        ("ovs", "ovsdb_connection", topology.ovsdb_connection()),
        ("ovn", "ovn_sb_connection", ovn.sb_connection()),
    )
    common.restart("neutron-ovn-metadata-agent")
