# Copyright 2026 - Canonical Ltd
# SPDX-License-Identifier: GPL-3.0-only

import pathlib

from regress_stack.core import utils as core_utils
from regress_stack.modules import keystone, mysql, rabbitmq
from regress_stack.modules import utils as module_utils

DEPENDENCIES = {keystone, mysql, rabbitmq}
PACKAGES = [
    "manila-api",
    "manila-scheduler",
    "manila-share",
    "python3-manilaclient",
    "lvm2",
    "nfs-kernel-server",
    "curl",
]
TEMPEST_PACKAGES = ["manila-tempest-plugin"]
LOGS = ["/var/log/manila/"]
TEST_INCLUDE_REGEXES = [
    r"manila_tempest_tests\.tests\.api\.test_(?:availability_zones|limits|microversions|rules|shares|shares_actions)\.",
]

SERVICE = "manila"
SERVICE_TYPE = "sharev2"
CONF = "/etc/manila/manila.conf"
BACKEND = "regress-lvm"
VOLUME_GROUP = "regress-stack-manila"
SHARE_TYPE = "regress-lvm"
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


def setup():
    _ensure_volume_group()
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
        ("DEFAULT", "enabled_share_backends", BACKEND),
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
            BACKEND,
            {
                "share_backend_name": BACKEND,
                "share_driver": "manila.share.drivers.lvm.LVMShareDriver",
                "driver_handles_share_servers": "False",
                "lvm_share_volume_group": VOLUME_GROUP,
                "lvm_share_export_ips": core_utils.my_ip(),
                "lvm_share_helpers": "NFS=manila.share.drivers.helpers.NFSHelper",
            },
        ),
    )
    core_utils.sudo("manila-manage", ["db", "sync"], SERVICE)
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
                f"share_backend_name={BACKEND}",
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
        ("share", "run_shrink_tests", "False"),
        ("share", "run_share_group_tests", "False"),
        ("share", "default_share_type_name", SHARE_TYPE),
    )
