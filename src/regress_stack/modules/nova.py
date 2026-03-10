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
BASE_PACKAGES = [
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

NOVA_APACHE_API_VERSION = "32.0.0"
NOVA_METADATA_SITE = pathlib.Path(
    "/etc/apache2/sites-available/regress-stack-nova-metadata.conf"
)
NOVA_METADATA_SITE_NAME = NOVA_METADATA_SITE.name
NOVA_SUDOERS = pathlib.Path("/etc/sudoers.d/regress-stack-nova-rootwrap")
NOVA_ROOTWRAP = pathlib.Path("/usr/bin/nova-rootwrap")
APACHE_SITES_ENABLED = pathlib.Path("/etc/apache2/sites-enabled")
NOVA_METADATA_PROCESS_GROUP = "nova-metadata"
NOVA_PRIVSEP_HELPER = (
    "sudo /usr/bin/nova-rootwrap /etc/nova/rootwrap.conf privsep-helper"
)


def determine_packages(no_tempest: bool = False) -> list[str]:
    return list(BASE_PACKAGES)


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
    _ensure_questing_compat()

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

    if _api_runs_under_apache():
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


def _api_runs_under_apache() -> bool:
    return (
        core_apt.PkgVersionCompare("python3-nova", upstream=True)
        >= NOVA_APACHE_API_VERSION
    )


def _ensure_questing_compat() -> None:
    if _using_sudo_rs():
        _ensure_sudo_rs_rootwrap()
        module_utils.cfg_set(
            CONF,
            ("nova_sys_admin", "helper_command", NOVA_PRIVSEP_HELPER),
            ("vif_plug_ovs_privileged", "helper_command", NOVA_PRIVSEP_HELPER),
        )
    if _api_runs_under_apache():
        _ensure_metadata_site()


def _using_sudo_rs() -> bool:
    result = subprocess.run(
        ["sudo", "-V"],
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    return "sudo-rs" in f"{result.stdout}\n{result.stderr}"


def _ensure_sudo_rs_rootwrap() -> None:
    if not NOVA_ROOTWRAP.exists():
        return
    contents = "nova ALL = (root) NOPASSWD: /usr/bin/nova-rootwrap\n"
    current = NOVA_SUDOERS.read_text() if NOVA_SUDOERS.exists() else None
    if current == contents:
        return
    core_utils.warn_workaround(
        "nova + sudo-rs",
        "installing a local sudoers override for nova-rootwrap until the Ubuntu package defaults are fixed",
    )
    NOVA_SUDOERS.write_text(contents)
    NOVA_SUDOERS.chmod(0o440)
    core_utils.run("visudo", ["-cf", str(NOVA_SUDOERS)])


def _enabled_apache_sites() -> list[pathlib.Path]:
    return sorted(APACHE_SITES_ENABLED.glob("*.conf"))


def _site_has_metadata(site_path: pathlib.Path) -> bool:
    if not site_path.exists():
        return False
    content = site_path.read_text()
    return all(
        marker in content
        for marker in (
            "Listen 8775",
            "/usr/bin/nova-metadata-wsgi",
            f"WSGIDaemonProcess {NOVA_METADATA_PROCESS_GROUP}",
            f"WSGIProcessGroup {NOVA_METADATA_PROCESS_GROUP}",
        )
    )


def _site_has_broken_metadata(site_path: pathlib.Path) -> bool:
    if not site_path.exists():
        return False
    content = site_path.read_text()
    return "Listen 8775" in content and "nova-api-metadata-wsgi" in content


def _ensure_metadata_site() -> None:
    for site in _enabled_apache_sites():
        if _site_has_metadata(site):
            return

    broken_site = APACHE_SITES_ENABLED / "nova-api-metadata.conf"
    if _site_has_broken_metadata(broken_site):
        core_utils.warn_workaround(
            "nova metadata packaging",
            "disabling the broken nova-api-metadata Apache site and enabling a local metadata vhost until the Ubuntu package is fixed",
        )
        core_utils.run("a2dissite", [broken_site.name])
    elif not NOVA_METADATA_SITE.exists():
        core_utils.warn_workaround(
            "nova metadata packaging",
            "installing a local metadata Apache vhost until the Ubuntu package split is fixed",
        )

    core_utils.write_resource(
        "regress_stack.resources",
        "nova-metadata.conf",
        NOVA_METADATA_SITE,
        overwrite=True,
    )
    if not (APACHE_SITES_ENABLED / NOVA_METADATA_SITE_NAME).exists():
        core_utils.run("a2ensite", [NOVA_METADATA_SITE_NAME])


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
