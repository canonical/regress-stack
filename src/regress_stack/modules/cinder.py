# Copyright 2025 - Canonical Ltd
# SPDX-License-Identifier: GPL-3.0-only

import pathlib
import subprocess

from regress_stack.core import apt as core_apt
from regress_stack.core import utils as core_utils
from regress_stack.modules import ceph, keystone, mysql, rabbitmq
from regress_stack.modules import utils as module_utils

DEPENDENCIES = {ceph, keystone, mysql, rabbitmq}
PACKAGES = ["cinder-api", "cinder-scheduler", "cinder-volume"]
LOGS = ["/var/log/cinder/"]

CONF = "/etc/cinder/cinder.conf"
URL = f"http://{core_utils.my_ip()}:8776/v3/%(project_id)s"
SERVICE = "cinder"
SERVICE_TYPE = "volumev3"
VOLUME_POOL = "volumes"
VOLUME_USER = VOLUME_POOL
CINDER_ROOTWRAP = pathlib.Path("/usr/bin/cinder-rootwrap")
CINDER_SUDOERS = pathlib.Path("/etc/sudoers.d/regress-stack-cinder-rootwrap")
CINDER_PRIVSEP_HELPER = (
    "sudo /usr/bin/cinder-rootwrap /etc/cinder/rootwrap.conf privsep-helper"
)


def installed() -> bool:
    return core_apt.pkgs_installed(PACKAGES)


def setup():
    db_user, db_pass = mysql.ensure_service(SERVICE)
    rabbit_user, rabbit_pass = rabbitmq.ensure_service(SERVICE)
    username, password = keystone.ensure_service_account(SERVICE, SERVICE_TYPE, URL)
    pool = ceph.ensure_pool(VOLUME_POOL)
    ceph.ensure_authenticate(VOLUME_POOL, SERVICE)
    core_utils.run(
        "sed",
        [
            "-i",
            "s|cinder-wsgi processes=5 threads=1|cinder-wsgi processes=1 threads=1|",
            "/etc/apache2/conf-enabled/cinder-wsgi.conf",
        ],
    )
    module_utils.cfg_set(
        CONF,
        (
            "database",
            "connection",
            mysql.connection_string(SERVICE, db_user, db_pass),
        ),
        ("DEFAULT", "my_ip", core_utils.my_ip()),
        ("DEFAULT", "transport_url", rabbitmq.transport_url(rabbit_user, rabbit_pass)),
        ("DEFAULT", "glance_api_version", "2"),
        ("DEFAULT", "enabled_backends", "ceph"),
        ("DEFAULT", "auth_strategy", "keystone"),
        *module_utils.dict_to_cfg_set_args(
            "keystone_authtoken", keystone.authtoken_service(username, password)
        ),
        ("oslo_concurrency", "lock_path", "/var/lib/cinder/tmp"),
        *module_utils.dict_to_cfg_set_args(
            "ceph",
            {
                "volume_driver": "cinder.volume.drivers.rbd.RBDDriver",
                "volume_backend_name": "ceph",
                "rbd_cluster_name": ceph.CLUSTER,
                "rbd_ceph_conf": ceph.CONF,
                "rbd_pool": pool,
                "rbd_user": pool,
                "rbd_secret_uuid": ceph.rbd_uuid(),
                "rbd_flatten_volume_from_snapshot": "false",
                "rbd_max_clone_depth": "5",
                "rbd_store_chunk_size": "4",
                "rbd_exclusive_cinder_pool": "true",
                "backend_host": f"{SERVICE}@{core_utils.fqdn()}",
            },
        ),
    )
    _ensure_questing_compat()
    core_utils.sudo("cinder-manage", ["db", "sync"], SERVICE)
    core_utils.restart_apache()
    core_utils.restart_service("cinder-scheduler")
    core_utils.restart_service("cinder-volume")


def _ensure_questing_compat() -> None:
    if not _using_sudo_rs():
        return
    _ensure_sudo_rs_rootwrap()
    module_utils.cfg_set(
        CONF,
        ("cinder_sys_admin", "helper_command", CINDER_PRIVSEP_HELPER),
    )


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
    if not CINDER_ROOTWRAP.exists():
        return
    contents = "cinder ALL = (root) NOPASSWD: /usr/bin/cinder-rootwrap\n"
    current = CINDER_SUDOERS.read_text() if CINDER_SUDOERS.exists() else None
    if current == contents:
        return
    core_utils.warn_workaround(
        "cinder + sudo-rs",
        "installing a local sudoers override for cinder-rootwrap until the Ubuntu package defaults are fixed",
    )
    CINDER_SUDOERS.write_text(contents)
    CINDER_SUDOERS.chmod(0o440)
    core_utils.run("visudo", ["-cf", str(CINDER_SUDOERS)])
