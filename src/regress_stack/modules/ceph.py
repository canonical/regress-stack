# Copyright 2025 - Canonical Ltd
# SPDX-License-Identifier: GPL-3.0-only

import functools
import logging
import shutil
import typing
import uuid
from pathlib import Path

from regress_stack.core import apt as core_apt
from regress_stack.core import utils as core_utils
from regress_stack.modules import utils as module_utils

LOG = logging.getLogger(__name__)

PACKAGES = ["ceph-mgr", "ceph-mon", "ceph-osd", "ceph-volume"]
LOGS = ["/var/log/ceph/"]

UUID_PATH = Path("/etc/ceph/fsid")

CLUSTER = "ceph"
CONF = f"/etc/ceph/{CLUSTER}.conf"
MON_KEYRING = Path("/etc/ceph/ceph.mon.keyring")
ADMIN_KEYRING = Path("/etc/ceph/ceph.client.admin.keyring")
OSD_KEYRING = Path("/var/lib/ceph/bootstrap-osd/ceph.keyring")
MONMAP = Path("/etc/ceph/ceph.monmap")
MON_DATA_FOLDER = Path(f"/var/lib/ceph/mon/{CLUSTER}-{core_utils.fqdn()}")
MON_SETUP_DONE = MON_DATA_FOLDER / "done"
MGR_DATA_FOLDER = Path(f"/var/lib/ceph/mgr/{CLUSTER}-{core_utils.fqdn()}")
MGR_KEYRING = MGR_DATA_FOLDER / "keyring"
MGR_SETUP_DONE = MGR_DATA_FOLDER / "done"
OSD_DATA_PATH = Path("/var/lib/ceph/osd")
RBD_UUID = Path("/etc/ceph/rbd_secret_uuid")

OSD_SIZE_GB = 2


def installed() -> bool:
    return core_apt.pkgs_installed(PACKAGES)


def setup():
    if not installed():
        return

    module_utils.cfg_set(
        CONF,
        *module_utils.dict_to_cfg_set_args(
            "global",
            {
                "fsid": ceph_uuid(),
                "mon host": core_utils.my_ip(),
                "mon initial members": core_utils.fqdn(),
                "public network": core_utils.my_network(),
                "auth cluster required": "cephx",
                "auth service required": "cephx",
                "auth client required": "cephx",
                "osd pool default size": "1",
                "osd pool default min size": "1",
                "mon warn on insecure global id reclaim": "false",
                "mon warn on insecure global id reclaim allowed": "false",
            },
        ),
    )
    ensure_ceph_folders()
    setup_mon_keyring()
    setup_mgr_keyring()
    setup_admin_keyring()
    setup_osd_keyring()
    import_keyrings()
    setup_mon()
    setup_mgr()
    for i in range(3):
        core_utils.exists_cache(OSD_DATA_PATH / f"ceph-{i}" / "done")(setup_osd)(i)


@functools.lru_cache
def ceph_uuid() -> str:
    if UUID_PATH.exists():
        return UUID_PATH.read_text().strip()
    uuid_str = str(uuid.uuid4())
    UUID_PATH.write_text(uuid_str)
    return uuid_str


@core_utils.exists_cache(MON_KEYRING)
def setup_mon_keyring() -> Path:
    core_utils.run(
        "ceph-authtool",
        [
            "--create-keyring",
            str(MON_KEYRING),
            "--gen-key",
            "-n",
            "mon.",
            "--cap",
            "mon",
            "allow *",
        ],
    )
    core_utils.run(
        "ln",
        ["-s", str(MON_KEYRING), "/etc/ceph/ceph.keyring"],
    )
    shutil.chown(MON_KEYRING, user="ceph", group="ceph")
    return MON_KEYRING


@core_utils.exists_cache(MGR_KEYRING)
def setup_mgr_keyring() -> Path:
    core_utils.run(
        "ceph-authtool",
        [
            "--create-keyring",
            str(MGR_KEYRING),
            "--gen-key",
            "-n",
            "mgr." + core_utils.fqdn(),
            "--cap",
            "mon",
            "allow profile mgr",
            "--cap",
            "osd",
            "allow *",
            "--cap",
            "mds",
            "allow *",
        ],
    )
    shutil.chown(MGR_KEYRING, user="ceph", group="ceph")
    return MGR_KEYRING


@core_utils.exists_cache(ADMIN_KEYRING)
def setup_admin_keyring() -> Path:
    core_utils.run(
        "ceph-authtool",
        [
            "--create-keyring",
            str(ADMIN_KEYRING),
            "--gen-key",
            "-n",
            "client.admin",
            "--cap",
            "mon",
            "allow *",
            "--cap",
            "osd",
            "allow *",
            "--cap",
            "mds",
            "allow *",
            "--cap",
            "mgr",
            "allow *",
        ],
    )
    return ADMIN_KEYRING


@core_utils.exists_cache(OSD_KEYRING)
def setup_osd_keyring() -> Path:
    core_utils.run(
        "ceph-authtool",
        [
            "--create-keyring",
            str(OSD_KEYRING),
            "--gen-key",
            "-n",
            "client.bootstrap-osd",
            "--cap",
            "mon",
            "profile bootstrap-osd",
            "--cap",
            "mgr",
            "allow r",
        ],
    )
    return OSD_KEYRING


def create_keyring(name: str, caps: str) -> Path:
    keyring = Path(f"/etc/ceph/ceph.{name}.keyring")
    if keyring.exists():
        return keyring

    return keyring


def import_keyrings():
    core_utils.run(
        "ceph-authtool",
        [
            str(setup_mon_keyring()),
            "--import-keyring",
            str(setup_mgr_keyring()),
        ],
    )
    core_utils.run(
        "ceph-authtool",
        [
            str(setup_mon_keyring()),
            "--import-keyring",
            str(setup_admin_keyring()),
        ],
    )
    core_utils.run(
        "ceph-authtool",
        [
            str(setup_mon_keyring()),
            "--import-keyring",
            str(setup_osd_keyring()),
        ],
    )


@core_utils.exists_cache(MONMAP)
def monmap() -> Path:
    core_utils.run(
        "monmaptool",
        [
            "--create",
            "--add",
            core_utils.fqdn(),
            core_utils.my_ip(),
            "--fsid",
            ceph_uuid(),
            str(MONMAP),
        ],
    )
    return MONMAP


def ensure_ceph_folders():
    MON_DATA_FOLDER.mkdir(parents=True, exist_ok=True)
    shutil.chown(MON_DATA_FOLDER, user="ceph", group="ceph")
    MGR_DATA_FOLDER.mkdir(parents=True, exist_ok=True)
    shutil.chown(MGR_DATA_FOLDER, user="ceph", group="ceph")


@core_utils.exists_cache(MON_SETUP_DONE)
def setup_mon():
    core_utils.sudo(
        "ceph-mon",
        [
            "--mkfs",
            "--id",
            core_utils.fqdn(),
            "--cluster",
            CLUSTER,
            "--monmap",
            str(monmap()),
            "--keyring",
            str(MON_KEYRING),
        ],
        "ceph",
    )
    MON_SETUP_DONE.touch()
    unit = f"ceph-mon@{core_utils.fqdn()}"
    core_utils.restart_service(unit)
    core_utils.enable_service(unit)
    return MON_SETUP_DONE


@core_utils.exists_cache(MGR_SETUP_DONE)
def setup_mgr():
    unit = f"ceph-mgr@{core_utils.fqdn()}"
    core_utils.restart_service(unit)
    core_utils.enable_service(unit)
    MGR_SETUP_DONE.touch()
    return MGR_SETUP_DONE


def setup_osd(i: int) -> Path:
    osd_dir = OSD_DATA_PATH / f"ceph-{i}"
    osd_dir.mkdir(parents=True, exist_ok=True)
    shutil.chown(osd_dir, "ceph", "ceph")

    backing_file = osd_dir / "backing.img"
    core_utils.run(
        "fallocate",
        ["--length", f"{OSD_SIZE_GB}G", str(backing_file)],
    )
    shutil.chown(backing_file, user="ceph", group="ceph")

    osd_id = core_utils.run("ceph", ["osd", "create"]).strip()

    core_utils.run(
        "ceph",
        [
            "auth",
            "get-or-create",
            f"osd.{osd_id}",
            "-o",
            str(osd_dir / "keyring"),
            "mon",
            "allow profile osd",
            "mgr",
            "allow profile osd",
            "osd",
            "allow *",
        ],
    )

    core_utils.sudo(
        "ceph-osd",
        [
            "--mkfs",
            "-i",
            osd_id,
            "--cluster",
            CLUSTER,
            "--keyring",
            str(OSD_KEYRING),
            "--no-mon-config",
            "--bluestore-block-path",
            str(backing_file),
        ],
        "ceph",
    )

    unit = f"ceph-osd@{osd_id}"
    core_utils.restart_service(unit)
    core_utils.enable_service(unit)
    done_file = osd_dir / "done"
    done_file.touch()
    return done_file


def ensure_pool(name: str) -> str:
    pools = core_utils.run("ceph", ["osd", "pool", "ls"]).splitlines()
    for pool in pools:
        if name == pool.strip():
            return name
    core_utils.run("ceph", ["osd", "pool", "create", name, "32"])
    return name


def ensure_authenticate(pool: str, user: typing.Optional[str] = None) -> Path:
    keyring = Path(f"/etc/ceph/ceph.client.{pool}.keyring")
    if keyring.exists():
        return keyring
    core_utils.run(
        "ceph-authtool",
        [
            "--create-keyring",
            str(keyring),
            "--gen-key",
            "-n",
            f"client.{pool}",
            "--cap",
            "mon",
            "profile rbd",
            "--cap",
            "osd",
            f"profile rbd pool={pool}",
        ],
    )
    core_utils.run(
        "ceph",
        ["auth", "import", "-i", str(keyring)],
    )
    if user:
        shutil.chown(keyring, user=user)
    keyring.chmod(0o600)
    return keyring


def get_key(user: str) -> str:
    return core_utils.run("ceph", ["auth", "get-key", f"client.{user}"]).strip()


@functools.lru_cache
def rbd_uuid() -> str:
    if RBD_UUID.exists():
        return RBD_UUID.read_text().strip()
    uuid_str = str(uuid.uuid4())
    RBD_UUID.write_text(uuid_str)
    return uuid_str
