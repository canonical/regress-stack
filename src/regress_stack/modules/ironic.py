# Copyright 2025 - Canonical Ltd
# SPDX-License-Identifier: GPL-3.0-only

import ipaddress
import json
import logging
import os
import pathlib
import subprocess
import textwrap
import time
import typing

from regress_stack.core import utils as core_utils
from regress_stack.modules import glance, keystone, mysql, neutron, nova, ovn, rabbitmq
from regress_stack.modules import utils as module_utils

LOG = logging.getLogger(__name__)

DEPENDENCIES = {glance, keystone, mysql, neutron, nova, ovn, rabbitmq}

BASE_PACKAGES = [
    "ironic-api",
    "ironic-conductor",
    "python3-ironicclient",
]
IPMI_PACKAGES = [
    "qemu-system-x86",
    "qemu-utils",
    "libvirt-clients",
    "libvirt-daemon-system",
    "ipmitool",
    "virtualbmc",
]
TEMPEST_PACKAGES = [
    "ironic-tempest-plugin",
]
LOGS = ["/var/log/ironic/"]

CONF = "/etc/ironic/ironic.conf"
SERVICE = "ironic"
SERVICE_TYPE = "baremetal"

BAREMETAL_VM_NAME = "regress-stack-ironic-node"
BAREMETAL_MAC = "52:54:00:12:34:56"
BAREMETAL_DISK_GB = 40
BAREMETAL_VCPUS = 2
BAREMETAL_RAM_MB = 4096
VBMC_PORT = 6230
VBMC_ADDR = "127.0.0.1"
VBMC_USER = "admin"
VBMC_PASS = "admin"
IRONIC_FLAVOR = "metallic-flavor"
IRONIC_IMAGE_KERNEL = "tinyipa-deploy-ipmi.vmlinuz"
IRONIC_IMAGE_INITRD = "tinyipa-deploy-ipmi.initramfs"
IRONIC_PROVISION_TIMEOUT = "600"
RESOURCE_CLASS = "baremetal"
IRONIC_VM_XML = pathlib.Path("/var/lib/regress-stack/ironic-vm.xml")
IRONIC_VM_DISK = pathlib.Path(f"/var/lib/libvirt/images/{BAREMETAL_VM_NAME}.qcow2")
IRONIC_DOWNLOAD_DIR = pathlib.Path("/var/lib/regress-stack/ironic")
SWITCH_INFO = "virtual"
SWITCH_ID = "00:00:00:00:00:00"
TINYIPA_KERNEL_URL = (
    "https://tarballs.openstack.org/ironic-python-agent/tinyipa/files/"
    "tinyipa-master.vmlinuz"
)
TINYIPA_INITRD_URL = (
    "https://tarballs.openstack.org/ironic-python-agent/tinyipa/files/tinyipa-master.gz"
)

TEST_EXCLUDE_REGEXES = [
    "test_show_deploy_template",
    "test_show_runbook",
    "test_driver_properties",
    "test_driver_logical_disk_properties",
    "test_reset_interfaces",
    "test_set_interfaces",
    "test_set_console_mode",
    "TestNodesVif",
    "TestNodeStatesV1_6.test_set_node_provision_state",
    "TestNodeStatesV1_11.test_set_node_provision_state",
    "test_set_node_raid_config",
    "TestAllocations",
]
PROFILE_FAKE_HARDWARE = "fake-hardware"
PROFILE_IPMI = "ipmi"
VALID_PROFILES = {PROFILE_FAKE_HARDWARE, PROFILE_IPMI}
SYSTEM_SCOPED_TEMPEST_RELEASES = {
    "caracal",
    "epoxy",
    "gazpacho",
    "hibiscus",
    "noble",
    "oracular",
    "plucky",
    "questing",
    "resolute",
}


def profile() -> str:
    if value := os.environ.get("IRONIC_PROFILE"):
        normalized = value.strip().lower()
        if normalized not in VALID_PROFILES:
            raise RuntimeError(
                f"Unknown IRONIC_PROFILE={value!r}, expected one of {sorted(VALID_PROFILES)}"
            )
        return normalized
    return PROFILE_FAKE_HARDWARE


def enabled() -> bool:
    return True


def tempest_regexes() -> tuple[list[str], list[str]]:
    if profile() == PROFILE_FAKE_HARDWARE:
        return ["ironic_tempest_plugin.tests.api.admin"], TEST_EXCLUDE_REGEXES
    return [], TEST_EXCLUDE_REGEXES


def _tempest_uses_system_scope() -> bool:
    return core_utils.release() in SYSTEM_SCOPED_TEMPEST_RELEASES


def determine_packages(no_tempest: bool = False) -> list[str]:
    packages = list(BASE_PACKAGES)
    if profile() == PROFILE_IPMI:
        packages.extend(IPMI_PACKAGES)
    if not no_tempest:
        packages.extend(TEMPEST_PACKAGES)
    return packages


def provisioning_ip() -> str:
    return str(next(ipaddress.ip_network(ovn.EXTERNAL_CIDR).hosts()))


def service_url() -> str:
    return f"http://{provisioning_ip()}:6385/"


def _openstack(args: typing.Sequence[str], system_scope: bool = True) -> str:
    env = keystone.system_auth_env() if system_scope else keystone.auth_env()
    return core_utils.run("openstack", list(args), env=env)


def _wait_for_api() -> None:
    for _ in range(12):
        try:
            _openstack(["baremetal", "driver", "list", "-f", "json"])
            return
        except Exception as exc:
            if not _is_retryable_api_error(exc):
                raise
            LOG.info("Waiting for ironic-api to start...")
            time.sleep(5)
    _openstack(["baremetal", "driver", "list", "-f", "json"])


def _is_retryable_api_error(exc: Exception) -> bool:
    if "Connection refused" in str(exc):
        return True
    if isinstance(exc, subprocess.CalledProcessError):
        stderr = exc.stderr or ""
        stdout = exc.stdout or ""
        return "Connection refused" in stderr or "Connection refused" in stdout
    return False


def _ensure_provisioning_network() -> str:
    neutron.ensure_public_network()
    conn = keystone.o7k()
    subnet = conn.network.find_subnet("external-subnet", ignore_missing=False)
    if not getattr(subnet, "is_dhcp_enabled", None):
        core_utils.run(
            "openstack",
            ["subnet", "set", "--dhcp", subnet.id],
            env=keystone.auth_env(),
        )
    network = conn.network.find_network(neutron.EXTERNAL_NETWORK, ignore_missing=False)
    return network.id


def _ensure_vm_xml() -> pathlib.Path:
    domain_type = nova.virt_type()
    content = textwrap.dedent(
        f"""\
        <domain type='{domain_type}'>
          <name>{BAREMETAL_VM_NAME}</name>
          <memory unit='MiB'>{BAREMETAL_RAM_MB}</memory>
          <vcpu>{BAREMETAL_VCPUS}</vcpu>
          <os>
            <type arch='x86_64' machine='pc'>hvm</type>
            <boot dev='network'/>
            <boot dev='hd'/>
          </os>
          <features>
            <acpi/>
            <apic/>
          </features>
          <cpu mode='host-model'/>
          <devices>
            <disk type='file' device='disk'>
              <driver name='qemu' type='qcow2'/>
              <source file='{IRONIC_VM_DISK}'/>
              <target dev='vda' bus='virtio'/>
            </disk>
            <interface type='bridge'>
              <mac address='{BAREMETAL_MAC}'/>
              <source bridge='{ovn.EXTERNAL_BRIDGE}'/>
              <virtualport type='openvswitch'/>
              <model type='e1000'/>
            </interface>
            <serial type='pty'>
              <target port='0'/>
            </serial>
            <console type='pty'>
              <target type='serial' port='0'/>
            </console>
            <graphics type='vnc' port='-1'/>
          </devices>
        </domain>
        """
    )
    IRONIC_VM_XML.parent.mkdir(parents=True, exist_ok=True)
    IRONIC_VM_XML.write_text(content)
    return IRONIC_VM_XML


def _ensure_baremetal_vm() -> None:
    core_utils.restart_service("libvirtd")
    if not IRONIC_VM_DISK.exists():
        core_utils.run(
            "qemu-img",
            ["create", "-f", "qcow2", str(IRONIC_VM_DISK), f"{BAREMETAL_DISK_GB}G"],
        )
    _ensure_vm_xml()
    domains = core_utils.run("virsh", ["list", "--all", "--name"]).splitlines()
    if BAREMETAL_VM_NAME not in domains:
        core_utils.run("virsh", ["define", str(IRONIC_VM_XML)])
    else:
        try:
            core_utils.run("virsh", ["destroy", BAREMETAL_VM_NAME])
        except Exception:
            pass
        core_utils.run("virsh", ["undefine", BAREMETAL_VM_NAME])
        core_utils.run("virsh", ["define", str(IRONIC_VM_XML)])
    core_utils.run("virsh", ["start", BAREMETAL_VM_NAME])
    core_utils.run("virsh", ["destroy", BAREMETAL_VM_NAME])


def _ensure_virtualbmc() -> None:
    try:
        core_utils.run("pkill", ["-f", "vbmcd"])
    except Exception:
        pass
    vbmc_dir = pathlib.Path("~/.vbmc").expanduser()
    if vbmc_dir.exists():
        for path in sorted(vbmc_dir.glob("*"), reverse=True):
            if path.is_file():
                path.unlink()
    core_utils.run("vbmcd")
    try:
        output = core_utils.run("vbmc", ["show", BAREMETAL_VM_NAME])
    except Exception:
        output = ""
    if BAREMETAL_VM_NAME not in output:
        core_utils.run(
            "vbmc",
            [
                "add",
                BAREMETAL_VM_NAME,
                "--port",
                str(VBMC_PORT),
                "--username",
                VBMC_USER,
                "--password",
                VBMC_PASS,
            ],
        )
    core_utils.run("vbmc", ["start", BAREMETAL_VM_NAME])
    core_utils.run(
        "ipmitool",
        [
            "-I",
            "lanplus",
            "-U",
            VBMC_USER,
            "-P",
            VBMC_PASS,
            "-H",
            VBMC_ADDR,
            "-p",
            str(VBMC_PORT),
            "power",
            "status",
        ],
    )


def _ensure_pxe_directories() -> None:
    for path in (
        pathlib.Path("/tftpboot"),
        pathlib.Path("/tftpboot/grub"),
        pathlib.Path("/httpboot"),
        pathlib.Path("/var/lib/ironic/tmp"),
    ):
        path.mkdir(parents=True, exist_ok=True)
    core_utils.run(
        "chown",
        [
            "-R",
            "ironic:ironic",
            "/tftpboot",
            "/httpboot",
            "/var/lib/ironic",
            "/var/log/ironic",
        ],
    )
    core_utils.run("chmod", ["755", "/tftpboot", "/tftpboot/grub", "/httpboot"])


def _ensure_tinyipa_images() -> typing.Tuple[str, str]:
    IRONIC_DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
    kernel_path = IRONIC_DOWNLOAD_DIR / pathlib.Path(TINYIPA_KERNEL_URL).name
    initrd_path = IRONIC_DOWNLOAD_DIR / pathlib.Path(TINYIPA_INITRD_URL).name
    if not kernel_path.exists():
        core_utils.run("wget", [TINYIPA_KERNEL_URL], cwd=str(IRONIC_DOWNLOAD_DIR))
    if not initrd_path.exists():
        core_utils.run("wget", [TINYIPA_INITRD_URL], cwd=str(IRONIC_DOWNLOAD_DIR))

    env = keystone.auth_env()
    for image_name, file_path in (
        (IRONIC_IMAGE_KERNEL, kernel_path),
        (IRONIC_IMAGE_INITRD, initrd_path),
    ):
        try:
            image_id = core_utils.run(
                "openstack",
                ["image", "show", image_name, "-f", "value", "-c", "id"],
                env=env,
            ).strip()
        except Exception:
            image_id = core_utils.run(
                "openstack",
                [
                    "image",
                    "create",
                    image_name,
                    "--public",
                    "--disk-format",
                    "raw",
                    "--container-format",
                    "bare",
                    "--file",
                    str(file_path),
                    "-f",
                    "value",
                    "-c",
                    "id",
                ],
                env=env,
            ).strip()
        if image_name == IRONIC_IMAGE_KERNEL:
            kernel_id = image_id
        else:
            initrd_id = image_id
    return kernel_id, initrd_id


def _ensure_ironic_flavor() -> str:
    flavor = nova.ensure_flavor(
        IRONIC_FLAVOR,
        ram=BAREMETAL_RAM_MB,
        vcpus=BAREMETAL_VCPUS,
        disk=BAREMETAL_DISK_GB,
    )
    core_utils.run(
        "openstack",
        [
            "flavor",
            "set",
            "--property",
            "resources:VCPU=0",
            "--property",
            "resources:MEMORY_MB=0",
            "--property",
            "resources:DISK_GB=0",
            "--property",
            "resources:CUSTOM_BAREMETAL=1",
            flavor.id,
        ],
        env=keystone.auth_env(),
    )
    return flavor.id


def _find_node(name: str) -> typing.Optional[dict]:
    try:
        output = _openstack(["baremetal", "node", "show", name, "-f", "json"])
    except Exception:
        return None
    return json.loads(output)


def _ensure_baremetal_node(network_id: str, kernel_id: str, initrd_id: str) -> None:
    node = _find_node(BAREMETAL_VM_NAME)
    if node is None:
        output = _openstack(
            [
                "baremetal",
                "node",
                "create",
                "--name",
                BAREMETAL_VM_NAME,
                "--driver",
                "ipmi",
                "--resource-class",
                RESOURCE_CLASS,
                "-f",
                "json",
            ]
        )
        node = json.loads(output)

    _openstack(
        [
            "baremetal",
            "node",
            "set",
            BAREMETAL_VM_NAME,
            "--driver-info",
            f"ipmi_address={VBMC_ADDR}",
            "--driver-info",
            f"ipmi_port={VBMC_PORT}",
            "--driver-info",
            f"ipmi_username={VBMC_USER}",
            "--driver-info",
            f"ipmi_password={VBMC_PASS}",
            "--driver-info",
            f"deploy_kernel={kernel_id}",
            "--driver-info",
            f"deploy_ramdisk={initrd_id}",
            "--driver-info",
            f"cleaning_network={network_id}",
            "--driver-info",
            f"provisioning_network={network_id}",
            "--property",
            f"cpus={BAREMETAL_VCPUS}",
            "--property",
            f"memory_mb={BAREMETAL_RAM_MB}",
            "--property",
            f"local_gb={BAREMETAL_DISK_GB}",
            "--property",
            "cpu_arch=x86_64",
        ]
    )

    try:
        _openstack(["baremetal", "port", "show", BAREMETAL_MAC, "-f", "json"])
    except Exception:
        port_uuid = _openstack(
            [
                "baremetal",
                "port",
                "create",
                BAREMETAL_MAC,
                "--node",
                node["uuid"],
                "-f",
                "value",
                "-c",
                "uuid",
            ]
        ).strip()
        _openstack(
            [
                "baremetal",
                "port",
                "set",
                port_uuid,
                "--local-link-connection",
                f"switch_info={SWITCH_INFO}",
                "--local-link-connection",
                f"switch_id={SWITCH_ID}",
                "--local-link-connection",
                f"port_id={BAREMETAL_MAC}",
            ]
        )


def _ensure_fake_hardware_node() -> None:
    node = _find_node(BAREMETAL_VM_NAME)
    if node is None:
        _openstack(
            [
                "baremetal",
                "node",
                "create",
                "--name",
                BAREMETAL_VM_NAME,
                "--driver",
                PROFILE_FAKE_HARDWARE,
                "--resource-class",
                RESOURCE_CLASS,
                "-f",
                "json",
            ]
        )

    _openstack(
        [
            "baremetal",
            "node",
            "set",
            BAREMETAL_VM_NAME,
            "--property",
            f"cpus={BAREMETAL_VCPUS}",
            "--property",
            f"memory_mb={BAREMETAL_RAM_MB}",
            "--property",
            f"local_gb={BAREMETAL_DISK_GB}",
            "--property",
            "cpu_arch=x86_64",
        ]
    )


def _ensure_node_available() -> None:
    _openstack(["baremetal", "node", "validate", BAREMETAL_VM_NAME])
    node = _find_node(BAREMETAL_VM_NAME)
    if node is None:
        raise RuntimeError("Ironic node was not created")
    state = node.get("provision_state")
    if state == "available":
        return
    if state == "enroll":
        _openstack(
            [
                "baremetal",
                "node",
                "manage",
                BAREMETAL_VM_NAME,
                "--wait",
                IRONIC_PROVISION_TIMEOUT,
            ]
        )
        state = "manageable"
    if state == "manageable":
        _openstack(
            [
                "baremetal",
                "node",
                "provide",
                BAREMETAL_VM_NAME,
                "--wait",
                IRONIC_PROVISION_TIMEOUT,
            ]
        )
    node = _find_node(BAREMETAL_VM_NAME)
    if node is None or node.get("provision_state") != "available":
        raise RuntimeError(f"Ironic node is not available: {node}")


def setup() -> None:
    db_user, db_pass = mysql.ensure_service(SERVICE)
    rabbit_user, rabbit_pass = rabbitmq.ensure_service(SERVICE)
    username, password = keystone.ensure_service_account(
        SERVICE, SERVICE_TYPE, service_url()
    )
    keystone.grant_system_role(keystone.ADMIN_USERNAME, "admin")
    cfg_args = [
        (
            "database",
            "connection",
            mysql.connection_string(SERVICE, db_user, db_pass),
        ),
        ("database", "max_pool_size", "1"),
        ("DEFAULT", "transport_url", rabbitmq.transport_url(rabbit_user, rabbit_pass)),
        ("DEFAULT", "host", core_utils.fqdn()),
        ("DEFAULT", "my_ip", provisioning_ip()),
        ("DEFAULT", "enabled_inspect_interfaces", "no-inspect"),
        ("DEFAULT", "enabled_console_interfaces", "no-console"),
        ("DEFAULT", "enabled_raid_interfaces", "no-raid"),
        *module_utils.dict_to_cfg_set_args(
            "keystone_authtoken", keystone.authtoken_service(username, password)
        ),
        *module_utils.dict_to_cfg_set_args(
            "service_user", keystone.account_dict(username, password)
        ),
        ("service_user", "send_service_user_token", "true"),
        *module_utils.dict_to_cfg_set_args(
            "nova", keystone.account_dict(username, password)
        ),
        *module_utils.dict_to_cfg_set_args(
            "neutron", keystone.account_dict(username, password)
        ),
        *module_utils.dict_to_cfg_set_args(
            "glance", keystone.account_dict(username, password)
        ),
        ("conductor", "automated_clean", "false"),
        ("oslo_concurrency", "lock_path", "/var/lib/ironic/tmp"),
    ]

    if profile() == PROFILE_IPMI:
        network_id = _ensure_provisioning_network()
        cfg_args.extend(
            [
                ("DEFAULT", "enabled_hardware_types", "ipmi,intel-ipmi"),
                ("DEFAULT", "enabled_boot_interfaces", "pxe"),
                ("DEFAULT", "enabled_deploy_interfaces", "direct"),
                ("DEFAULT", "enabled_power_interfaces", "ipmitool"),
                ("DEFAULT", "enabled_management_interfaces", "ipmitool,intel-ipmitool"),
                ("DEFAULT", "enabled_network_interfaces", "neutron,noop"),
                ("DEFAULT", "default_network_interface", "neutron"),
                ("DEFAULT", "enabled_vendor_interfaces", "ipmitool,no-vendor"),
                ("DEFAULT", "enabled_bios_interfaces", "no-bios"),
                ("neutron", "cleaning_network", network_id),
                ("neutron", "provisioning_network", network_id),
                ("pxe", "tftp_root", "/tftpboot"),
                ("pxe", "tftp_server", provisioning_ip()),
                (
                    "pxe",
                    "kernel_append_params",
                    "nofb nomodeset vga=normal console=tty0 console=ttyS0,115200n8",
                ),
                ("agent", "image_download_source", "http"),
            ]
        )
    else:
        cfg_args.extend(
            [
                ("DEFAULT", "enabled_hardware_types", PROFILE_FAKE_HARDWARE),
                ("DEFAULT", "enabled_boot_interfaces", "fake"),
                ("DEFAULT", "enabled_deploy_interfaces", "fake"),
                ("DEFAULT", "enabled_power_interfaces", "fake"),
                ("DEFAULT", "enabled_management_interfaces", "fake"),
                ("DEFAULT", "enabled_network_interfaces", "noop"),
                ("DEFAULT", "default_network_interface", "noop"),
                ("DEFAULT", "enabled_vendor_interfaces", "no-vendor"),
                ("DEFAULT", "enabled_bios_interfaces", "no-bios"),
            ]
        )

    module_utils.cfg_set(CONF, *cfg_args)
    if profile() == PROFILE_IPMI:
        _ensure_pxe_directories()
    core_utils.sudo("ironic-dbsync", ["upgrade"], user="ironic")
    if profile() == PROFILE_IPMI:
        core_utils.restart_service("libvirtd")
    core_utils.restart_service("ironic-api")
    core_utils.restart_service("ironic-conductor")
    _wait_for_api()


def configure_tempest(tempest_conf: pathlib.Path) -> None:
    flavor_id = _ensure_ironic_flavor()
    tempest_driver = PROFILE_FAKE_HARDWARE
    tempest_hardware_types = PROFILE_FAKE_HARDWARE
    tempest_enabled_drivers = "fake,fake-hardware"
    if profile() == PROFILE_IPMI:
        tempest_driver = "ipmi"
        tempest_hardware_types = "ipmi,intel-ipmi"
        tempest_enabled_drivers = "ipmi,intel-ipmi"
    cfg_args = [
        ("service_available", "baremetal", "True"),
        ("service_available", "ironic", "True"),
        ("baremetal", "reschedule_wait_timeout", "300"),
        ("baremetal", "flavor_ref", flavor_id),
        ("baremetal", "driver", tempest_driver),
        ("baremetal", "enabled_drivers", tempest_enabled_drivers),
        ("baremetal", "enabled_hardware_types", tempest_hardware_types),
    ]
    if _tempest_uses_system_scope():
        cfg_args.extend(
            [
                ("auth", "admin_system", "all"),
                ("enforce_scope", "ironic", "True"),
            ]
        )
    module_utils.cfg_set(str(tempest_conf), *cfg_args)


def smoke_test(_workspace_dir: pathlib.Path) -> None:
    _wait_for_api()
    if profile() == PROFILE_IPMI:
        _ensure_pxe_directories()
        _ensure_baremetal_vm()
        _ensure_virtualbmc()
        network_id = _ensure_provisioning_network()
        kernel_id, initrd_id = _ensure_tinyipa_images()
        _ensure_baremetal_node(network_id, kernel_id, initrd_id)
    else:
        _ensure_fake_hardware_node()
    _ensure_ironic_flavor()
    _ensure_node_available()
