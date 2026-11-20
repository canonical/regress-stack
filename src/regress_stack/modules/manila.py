# Copyright 2026 - Canonical Ltd
# SPDX-License-Identifier: GPL-3.0-only

import importlib.util
import os
import pathlib
import shutil

from regress_stack.core import utils as core_utils
from regress_stack.modules import ceph, keystone, mysql, rabbitmq
from regress_stack.modules import utils as module_utils

BACKEND = os.environ.get("MANILA_BACKEND") or "lvm"
CEPHFS_NFS = BACKEND == "cephfs-nfs"
CEPHFS_NFS_MODE = os.environ.get("MANILA_CEPHFS_NFS_MODE") or "cluster"
DEPENDENCIES = {keystone, mysql, rabbitmq}
if CEPHFS_NFS:
    DEPENDENCIES.add(ceph)

PACKAGES = [
    "manila-api",
    "manila-scheduler",
    "manila-share",
    "python3-manilaclient",
    "curl",
]
if CEPHFS_NFS:
    PACKAGES.extend(
        [
            "ceph-mds",
            "nfs-common",
            "python3-ceph-argparse",
            "python3-rados",
        ]
    )
    if CEPHFS_NFS_MODE == "cluster":
        PACKAGES.extend(["cephadm", "podman", "openssh-server"])
    else:
        PACKAGES.extend(["nfs-ganesha", "nfs-ganesha-ceph"])
else:
    PACKAGES.extend(["lvm2", "nfs-kernel-server"])

TEMPEST_PACKAGES = ["manila-tempest-plugin"]
LOGS = ["/var/log/manila/"]
if CEPHFS_NFS:
    LOGS.append("/var/log/ganesha/")
TEST_INCLUDE_REGEXES = [
    r"manila_tempest_tests\.tests\.api\.test_(?:availability_zones|limits|microversions|rules|shares|shares_actions)\.",
]

SERVICE = "manila"
SERVICE_TYPE = "sharev2"
CONF = "/etc/manila/manila.conf"
SHARE_TYPE = f"regress-{BACKEND}"
VOLUME_GROUP = "regress-stack-manila"
FILESYSTEM = "regress-manila"
GANESHA_POOL = "regress-manila-ganesha"
GANESHA_INDEX = "ganesha-export-index"
GANESHA_CONF = pathlib.Path("/etc/ganesha/ganesha.conf")
GANESHA_EXPORTS = pathlib.Path("/var/lib/manila/ganesha-exports")
LOCK_PATH = "/var/lib/manila/lock"
ROOTWRAP_CONF = "/etc/manila/rootwrap.conf"
API_WSGI = pathlib.Path("/usr/bin/manila-api-wsgi")
APACHE_CONF = pathlib.Path("/etc/apache2/conf-available/regress-stack-manila.conf")
BACKING_FILE = pathlib.Path("/var/lib/regress-stack/manila-volumes.img")


def _api_url() -> str:
    return f"http://{core_utils.my_ip()}:8786"


def _volume_group_exists() -> bool:
    names = core_utils.run("vgs", ["--noheadings", "-o", "vg_name"])
    return VOLUME_GROUP in names.split()


def _ensure_volume_group() -> None:
    if _volume_group_exists():
        core_utils.run("vgchange", ["-ay", VOLUME_GROUP])
        return

    BACKING_FILE.parent.mkdir(parents=True, exist_ok=True)
    if not BACKING_FILE.exists():
        with BACKING_FILE.open("xb") as image:
            BACKING_FILE.chmod(0o600)
            image.truncate(16 * 1024**3)
    path = str(BACKING_FILE)
    device = core_utils.run(
        "losetup", ["--list", "--noheadings", "--output", "NAME", "-j", path]
    ).strip()
    if not device:
        device = core_utils.run(
            "losetup", ["--find", "--show", "--nooverlap", path]
        ).strip()

    # A previously configured image can be reattached after a reboot.
    if not _volume_group_exists():
        core_utils.run("vgcreate", [VOLUME_GROUP, device])
    core_utils.run("vgchange", ["-ay", VOLUME_GROUP])


def _check_cluster_support() -> None:
    """Reject old Manila packages without silently changing the selected helper."""
    package = importlib.util.find_spec("manila")
    if package is None or package.origin is None:
        raise RuntimeError("Install the Manila service packages before setup")
    driver = pathlib.Path(package.origin).parent / "share/drivers/cephfs/driver.py"
    if "class NFSClusterProtocolHelper(" not in driver.read_text():
        raise RuntimeError(
            "Installed Manila has no NFSClusterProtocolHelper; "
            "use MANILA_CEPHFS_NFS_MODE=standalone (required for Yoga/Jammy)"
        )


def _configure_cephfs_nfs() -> dict[str, str]:
    if CEPHFS_NFS_MODE == "cluster":
        _check_cluster_support()
    # Add a fourth OSD because parallel Tempest tests can reserve more than
    # the default 6 GiB. OSD_COUNT is the next index after the base OSDs.
    ceph.ensure_osd(i=ceph.OSD_COUNT, size_gb=8)
    ceph.ensure_filesystem(FILESYSTEM)
    core_utils.run("ceph", ["mgr", "module", "enable", "volumes"])
    settings = {
        "share_driver": "manila.share.drivers.cephfs.driver.CephFSDriver",
        "cephfs_conf_path": ceph.CONF,
        "cephfs_auth_id": SERVICE,
        "cephfs_cluster_name": ceph.CLUSTER,
        "cephfs_filesystem_name": FILESYSTEM,
        "cephfs_protocol_helper_type": "NFS",
    }
    keyring = pathlib.Path(f"/etc/ceph/{ceph.CLUSTER}.client.manila.keyring")
    caps = ["mon", "allow r", "mgr", "allow rw"]
    if CEPHFS_NFS_MODE == "standalone":
        caps.extend(["osd", f"allow rw pool={GANESHA_POOL}"])
    core_utils.run(
        "ceph", ["auth", "get-or-create", "client.manila", "-o", str(keyring)]
    )
    core_utils.run("ceph", ["auth", "caps", "client.manila", *caps])
    shutil.chown(keyring, user=SERVICE)
    keyring.chmod(0o600)
    if core_utils.has_service("nfs-server"):
        core_utils.run("systemctl", ["disable", "--now", "nfs-server"])

    if CEPHFS_NFS_MODE == "cluster":
        if core_utils.has_service("nfs-ganesha"):
            core_utils.run("systemctl", ["disable", "--now", "nfs-ganesha"])
        ceph.ensure_nfs_cluster(FILESYSTEM)
        return {**settings, "cephfs_nfs_cluster_id": FILESYSTEM}

    return {**settings, **_configure_standalone_ganesha()}


def _configure_standalone_ganesha() -> dict[str, str]:
    # Retain NFSProtocolHelper for distro-package regression tests. Upstream
    # deprecated this helper; cluster mode uses NFSClusterProtocolHelper.
    # The driver selects standalone only when the cluster ID is None, not
    # an empty string. Remove the option rather than writing a blank value.
    core_utils.run("crudini", ["--del", CONF, SHARE_TYPE, "cephfs_nfs_cluster_id"])
    ceph.ensure_pool(GANESHA_POOL)
    core_utils.run(
        "ceph", ["osd", "pool", "application", "enable", GANESHA_POOL, "nfs"]
    )
    objects = core_utils.run("rados", ["-p", GANESHA_POOL, "ls"]).splitlines()
    if GANESHA_INDEX not in objects:
        core_utils.run("rados", ["-p", GANESHA_POOL, "put", GANESHA_INDEX, "/dev/null"])
    GANESHA_EXPORTS.mkdir(parents=True, exist_ok=True)
    GANESHA_CONF.write_text(
        "NFS_CORE_PARAM { Protocols = 4; Enable_NLM = false; Enable_RQUOTA = false; }\n"
        f'RADOS_URLS {{ ceph_conf = "{ceph.CONF}"; userid = "{SERVICE}"; }}\n'
        f'%url "rados://{GANESHA_POOL}/{GANESHA_INDEX}"\n'
    )
    core_utils.restart_service("nfs-ganesha")
    core_utils.enable_service("nfs-ganesha")

    return {
        "cephfs_ganesha_server_is_remote": "False",
        "cephfs_ganesha_server_ip": core_utils.my_ip(),
        "ganesha_config_path": str(GANESHA_CONF),
        "ganesha_export_dir": str(GANESHA_EXPORTS),
        "ganesha_rados_store_enable": "True",
        "ganesha_rados_store_pool_name": GANESHA_POOL,
        "ganesha_rados_export_index": GANESHA_INDEX,
    }


def _configure_lvm() -> dict[str, str]:
    _ensure_volume_group()
    return {
        "share_driver": "manila.share.drivers.lvm.LVMShareDriver",
        "lvm_share_volume_group": VOLUME_GROUP,
        "lvm_share_export_ips": core_utils.my_ip(),
        "lvm_share_helpers": "NFS=manila.share.drivers.helpers.NFSHelper",
    }


def setup():
    if BACKEND not in {"lvm", "cephfs-nfs"}:
        raise ValueError(
            f"Unsupported MANILA_BACKEND {BACKEND!r}; use lvm or cephfs-nfs"
        )
    if CEPHFS_NFS and CEPHFS_NFS_MODE not in {"cluster", "standalone"}:
        raise ValueError(
            f"Unsupported MANILA_CEPHFS_NFS_MODE {CEPHFS_NFS_MODE!r}; "
            "use cluster or standalone"
        )
    backend_settings = _configure_cephfs_nfs() if CEPHFS_NFS else _configure_lvm()
    db_user, db_pass = mysql.ensure_service(SERVICE)
    rabbit_user, rabbit_pass = rabbitmq.ensure_service(SERVICE)
    username, password = keystone.ensure_service_account(
        SERVICE, SERVICE_TYPE, f"{_api_url()}/v2"
    )
    core_utils.run("install", ["-d", "-o", SERVICE, "-g", SERVICE, LOCK_PATH])
    module_utils.cfg_set(
        CONF,
        ("database", "connection", mysql.connection_string(SERVICE, db_user, db_pass)),
        ("DEFAULT", "transport_url", rabbitmq.transport_url(rabbit_user, rabbit_pass)),
        ("DEFAULT", "auth_strategy", "keystone"),
        ("DEFAULT", "enabled_share_backends", SHARE_TYPE),
        ("DEFAULT", "enabled_share_protocols", "NFS"),
        ("DEFAULT", "default_share_type", SHARE_TYPE),
        ("DEFAULT", "osapi_share_workers", "1"),
        ("DEFAULT", "rootwrap_config", ROOTWRAP_CONF),
        ("oslo_concurrency", "lock_path", LOCK_PATH),
        (
            "manila_sys_admin",
            "helper_command",
            f"sudo /usr/bin/manila-rootwrap {ROOTWRAP_CONF} privsep-helper --config-file {CONF}",
        ),
        *module_utils.dict_to_cfg_set_args(
            "keystone_authtoken", keystone.authtoken_service(username, password)
        ),
        *module_utils.dict_to_cfg_set_args(
            SHARE_TYPE,
            {
                "share_backend_name": SHARE_TYPE,
                "driver_handles_share_servers": "False",
                **backend_settings,
            },
        ),
    )
    if CEPHFS_NFS:
        # The scheduler decrements its free-space estimate on each share
        # request. Refresh it promptly on the small test Ceph cluster.
        module_utils.cfg_set(
            CONF,
            ("DEFAULT", "periodic_interval", "2"),
            ("DEFAULT", "periodic_fuzzy_delay", "0"),
        )

    core_utils.sudo("manila-manage", ["db", "sync"], SERVICE)
    if not CEPHFS_NFS:
        if core_utils.has_service("nfs-ganesha"):
            core_utils.run("systemctl", ["disable", "--now", "nfs-ganesha"])
            core_utils.enable_service("nfs-server")
        core_utils.restart_service("nfs-kernel-server")
    core_utils.restart_service("manila-scheduler", "manila-share")
    _start_api()
    _ensure_share_type()


def _start_api() -> None:
    if not API_WSGI.exists():
        core_utils.restart_service("manila-api")
        return

    # Newer Manila releases use a threading backend and require external WSGI.
    if core_utils.has_service("manila-api"):
        core_utils.run("systemctl", ["disable", "--now", "manila-api"])
    APACHE_CONF.write_text(
        f"""Listen 8786
<VirtualHost *:8786>
    WSGIDaemonProcess regress-stack-manila user=manila group=manila processes=1 threads=4
    WSGIProcessGroup regress-stack-manila
    WSGIApplicationGroup %{{GLOBAL}}
    WSGIPassAuthorization On
    WSGIScriptAlias / {API_WSGI}
    <Directory /usr/bin>
        <Files manila-api-wsgi>
            Require all granted
        </Files>
    </Directory>
</VirtualHost>
"""
    )
    core_utils.run("a2enconf", [APACHE_CONF.stem])
    core_utils.graceful_apache()


def _ensure_share_type() -> None:
    env = keystone.auth_env()
    core_utils.run(
        "curl",
        [
            "--fail",
            "--silent",
            "--show-error",
            "--retry",
            "10",
            "--retry-connrefused",
            "--retry-max-time",
            "30",
            "--max-time",
            "10",
            "--output",
            "/dev/null",
            f"{_api_url()}/",
        ],
    )
    types = core_utils.run(
        "openstack", ["share", "type", "list", "-f", "value", "-c", "Name"], env=env
    )
    if SHARE_TYPE not in types.splitlines():
        core_utils.run(
            "openstack",
            [
                "share",
                "type",
                "create",
                SHARE_TYPE,
                "false",
                "--extra-specs",
                f"share_backend_name={SHARE_TYPE}",
            ],
            env=env,
        )


def configure_tempest(tempest_conf: pathlib.Path):
    module_utils.cfg_set(
        str(tempest_conf),
        ("service_available", "manila", "True"),
        ("share", "catalog_type", SERVICE_TYPE),
        ("share", "multitenancy_enabled", "False"),
        ("share", "enable_protocols", "nfs"),
        ("share", "enable_ip_rules_for_protocols", "nfs"),
        # The LVM driver implements extend_share but does not implement shrink_share.
        ("share", "run_shrink_tests", str(CEPHFS_NFS)),
        ("share", "run_share_group_tests", "False"),
        ("share", "default_share_type_name", SHARE_TYPE),
    )
