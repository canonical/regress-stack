# Copyright 2025 - Canonical Ltd
# SPDX-License-Identifier: GPL-3.0-only

import json
import logging
import os
import pathlib
import stat
import subprocess
import time

from regress_stack.core import apt as core_apt
from regress_stack.core import utils as core_utils
from regress_stack.modules import (
    ceph,
    cinder,
    glance,
    keystone,
    mysql,
    neutron,
    ovn,
    placement,
    rabbitmq,
    utils,
)
from regress_stack.modules import utils as module_utils

DEPENDENCIES = {
    glance,
    keystone,
    mysql,
    neutron,
    ovn,
    rabbitmq,
    placement,
}
OPTIONAL_DEPENDENCIES = {ceph, cinder}
PACKAGES = [
    "nova-api",
    "nova-conductor",
    "nova-scheduler",
    "nova-compute",
    "nova-spiceproxy",
    "spice-html5",
]
LOG = logging.getLogger(__name__)

CONF = "/etc/nova/nova.conf"
URL = f"http://{core_utils.my_ip()}:8774/v2.1"
NOVA_CEPH_UUID = pathlib.Path("/etc/nova/ceph_uuid")
SERVICE = "nova"
SERVICE_TYPE = "compute"

NOVA_FLAMINGO_VERSION = "3:31.0.0+git2025070714.1c03429337-0ubuntu1"


def setup():
    db_user, db_pass = mysql.ensure_service(SERVICE)
    db_api_user, db_api_pass = mysql.ensure_service("nova_api")
    db_cell0_user, db_cell0_pass = mysql.ensure_service("nova_cell0")
    rabbit_user, rabbit_pass = rabbitmq.ensure_service(SERVICE)
    username, password = keystone.ensure_service_account(SERVICE, SERVICE_TYPE, URL)
    module_utils.cfg_set(
        CONF,
        (
            "database",
            "connection",
            mysql.connection_string(SERVICE, db_user, db_pass),
        ),
        ("database", "max_pool_size", "1"),
        (
            "api_database",
            "connection",
            mysql.connection_string("nova_api", db_api_user, db_api_pass),
        ),
        ("api_database", "max_pool_size", "1"),
        ("DEFAULT", "transport_url", rabbitmq.transport_url(rabbit_user, rabbit_pass)),
        ("DEFAULT", "host", core_utils.fqdn()),
        ("DEFAULT", "my_ip", core_utils.my_ip()),
        ("DEFAULT", "osapi_compute_workers", "1"),
        ("DEFAULT", "metadata_workers", "1"),
        ("conductor", "workers", "1"),
        ("scheduler", "workers", "1"),
        ("DEFAULT", "auth_strategy", "keystone"),
        *module_utils.dict_to_cfg_set_args(
            "keystone_authtoken", keystone.authtoken_service(username, password)
        ),
        *module_utils.dict_to_cfg_set_args(
            "placement", keystone.account_dict(username, password)
        ),
        *module_utils.dict_to_cfg_set_args(
            "neutron", keystone.account_dict(username, password)
        ),
        ("neutron", "service_metadata_proxy", "true"),
        ("neutron", "metadata_proxy_shared_secret", neutron.METADATA_SECRET),
        *module_utils.dict_to_cfg_set_args(
            "service_user", keystone.account_dict(username, password)
        ),
        ("service_user", "send_service_user_token", "true"),
        *module_utils.dict_to_cfg_set_args(
            "glance",
            {
                "service_type": glance.SERVICE_TYPE,
                "service_name": glance.SERVICE,
                "region_name": utils.REGION,
            },
        ),
        ("oslo_concurrency", "lock_path", "/var/lib/nova/tmp"),
        ("os_region_name", "openstack", utils.REGION),
        ("vnc", "enabled", "false"),
        *module_utils.dict_to_cfg_set_args(
            "spice",
            {
                "enabled": "true",
                "agent_enabled": "true",
                "html5proxy_base_url": f"http://{core_utils.my_ip()}:6082/spice_auto.html",
                "server_listen": core_utils.my_ip(),
                "server_proxyclient_address": core_utils.my_ip(),
                "keymap": "en-us",
            },
        ),
        *module_utils.dict_to_cfg_set_args(
            "libvirt",
            {
                "virt_type": virt_type(),
            },
        ),
        ("os_vif_ovs", "ovsdb_connection", ovn.OVSDB_CONNECTION),
    )

    if ceph.installed() and cinder.installed():
        pool = ceph.ensure_pool(cinder.VOLUME_POOL)
        module_utils.cfg_set(
            CONF,
            *module_utils.dict_to_cfg_set_args(
                "libvirt",
                {
                    "virt_type": virt_type(),
                    "rbd_user": pool,
                    "rbd_secret_uuid": ensure_libvirt_ceph_secret(),
                    "images_rbd_pool": pool,
                },
            ),
            *module_utils.dict_to_cfg_set_args(
                "cinder",
                {
                    "service_type": cinder.SERVICE_TYPE,
                    "service_name": cinder.SERVICE,
                    "region_name": utils.REGION,
                    "volume_api_version": "3",
                },
            ),
        )

    core_utils.sudo("nova-manage", ["api_db", "sync"], user="nova")
    core_utils.sudo(
        "nova-manage",
        [
            "cell_v2",
            "map_cell0",
            "--database_connection",
            mysql.connection_string("nova_cell0", db_cell0_user, db_cell0_pass),
        ],
        user="nova",
    )
    list_cells = core_utils.sudo("nova-manage", ["cell_v2", "list_cells"], user="nova")
    if " cell1 " not in list_cells:
        core_utils.sudo(
            "nova-manage", ["cell_v2", "create_cell", "--name=cell1"], user="nova"
        )
    core_utils.sudo("nova-manage", ["db", "sync"], user="nova")

    nova_daemons = ["nova-api", "nova-scheduler", "nova-conductor", "nova-compute"]

    if core_apt.PkgVersionCompare("python3-nova") >= NOVA_FLAMINGO_VERSION:
        nova_daemons.remove("nova-api")
        # nova-api runs under apache2 as a WSGI application
        nova_daemons.insert(0, "apache2")

    for _daemon in nova_daemons:
        core_utils.restart_service(_daemon)

    # Give some time for nova-compute to be up before discovering hosts
    for _ in range(25):
        output = core_utils.sudo(
            "nova-manage", ["cell_v2", "discover_hosts", "--verbose"], user="nova"
        )
        if core_utils.fqdn() in output:
            break
        output = core_utils.sudo("nova-manage", ["cell_v2", "list_hosts"], user="nova")
        if core_utils.fqdn() in output:
            break
        time.sleep(5)


def virt_type() -> str:
    if _is_hw_virt_supported() and _is_kvm_api_available():
        return "kvm"
    return "qemu"


def _is_kvm_api_available() -> bool:
    """Determine whether KVM is supportable."""
    kvm_devpath = "/dev/kvm"
    if not os.path.exists(kvm_devpath):
        LOG.warning(f"{kvm_devpath} does not exist")
        return False
    elif not os.access(kvm_devpath, os.R_OK | os.W_OK):
        LOG.warning(f"{kvm_devpath} is not RW-accessible")
        return False
    kvm_dev = os.stat(kvm_devpath)
    if not stat.S_ISCHR(kvm_dev.st_mode):
        LOG.warning(f"{kvm_devpath} is not a character device")
        return False
    major = os.major(kvm_dev.st_rdev)
    minor = os.minor(kvm_dev.st_rdev)
    if major != 10:
        LOG.warning(f"{kvm_devpath} has an unexpected major number: {major}")
        return False
    elif minor != 232:
        LOG.warning(f"{kvm_devpath} has an unexpected minor number: {minor}")
        return False
    return True


def _is_hw_virt_supported() -> bool:
    """Determine whether hardware virt is supported."""
    cpu_info = json.loads(core_utils.run("lscpu", ["-J"]))["lscpu"]
    architecture = next(
        filter(lambda x: x["field"] == "Architecture:", cpu_info), {"data": ""}
    )["data"].split()
    flags = next(filter(lambda x: x["field"] == "Flags:", cpu_info), None)
    if flags is not None:
        flags = flags["data"].split()

    vendor_id = next(filter(lambda x: x["field"] == "Vendor ID:", cpu_info), None)
    if vendor_id is not None:
        vendor_id = vendor_id["data"]

    # Mimic virt-host-validate code (from libvirt) and assume nested
    # support on ppc64 LE or BE.
    if architecture in ["ppc64", "ppc64le"]:
        return True
    elif vendor_id is not None and flags is not None:
        if vendor_id == "AuthenticAMD" and "svm" in flags:
            return True
        elif vendor_id == "GenuineIntel" and "vmx" in flags:
            return True
        elif vendor_id == "IBM/S390" and "sie" in flags:
            return True
        elif vendor_id == "ARM":
            # ARM 8.3-A added nested virtualization support but it is yet
            # to land upstream https://lwn.net/Articles/812280/ at the time
            # of writing (Nov 2020).
            LOG.warning(
                "Nested virtualization is not supported on ARM - will use emulation"
            )
            return False
        else:
            LOG.warning(
                "Unable to determine hardware virtualization"
                f' support by CPU vendor id "{vendor_id}":'
                " assuming it is not supported."
            )
            return False
    else:
        LOG.warning(
            "Unable to determine hardware virtualization support"
            " by the output of lscpu: assuming it is not"
            " supported"
        )
        return False


SECRET_TEMPLATE = """<secret ephemeral='no' private='no'>
  <uuid>{uuid}</uuid>
  <description>Ceph secret for Nova</description>
  <usage type='ceph'>
    <name>client.{user} secret</name>
  </usage>
</secret>
"""


def ensure_libvirt_ceph_secret() -> str:
    secret_uuid = ceph.rbd_uuid()
    try:
        core_utils.run("virsh", ["secret-get-value", secret_uuid])
        return secret_uuid
    except subprocess.CalledProcessError:
        pass
    template = pathlib.Path("/tmp/secret.xml")
    template.write_text(
        SECRET_TEMPLATE.format(uuid=secret_uuid, user=cinder.VOLUME_USER)
    )
    core_utils.run("virsh", ["secret-define", "--file", str(template)])
    core_utils.run(
        "virsh",
        [
            "secret-set-value",
            "--secret",
            secret_uuid,
            "--base64",
            ceph.get_key(cinder.VOLUME_USER),
        ],
    )
    return secret_uuid


def ensure_flavor(name: str, ram: int, vcpus: int, disk: int):
    """Ensure a flavor exists."""
    conn = keystone.o7k()
    flavor = conn.compute.find_flavor(name, ignore_missing=True)
    if flavor:
        return flavor
    return conn.compute.create_flavor(name=name, vcpus=vcpus, ram=ram, disk=disk)
