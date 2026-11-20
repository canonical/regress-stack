# Copyright 2025 - Canonical Ltd
# SPDX-License-Identifier: GPL-3.0-only

import functools
import json
import logging
import re
import shutil
import socket
import time
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

OSD_COUNT = 3
OSD_SIZE_GB = 2
MDS_START_TIMEOUT = 300


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
    for i in range(OSD_COUNT):
        ensure_osd(i)


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


def setup_mgr_keyring() -> Path:
    managed_keyring = _managed_mgr_folder() / "keyring"
    if managed_keyring.exists():
        return managed_keyring
    if MGR_KEYRING.exists():
        return MGR_KEYRING
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
    if not _managed_mgr_folder().exists():
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


def setup_mgr():
    managed = _managed_mgr_folder()
    if managed.exists():
        return managed / "done"
    if MGR_SETUP_DONE.exists():
        return MGR_SETUP_DONE
    unit = f"ceph-mgr@{core_utils.fqdn()}"
    core_utils.restart_service(unit)
    core_utils.enable_service(unit)
    MGR_SETUP_DONE.touch()
    return MGR_SETUP_DONE


def ensure_osd(i: int, size_gb: int = OSD_SIZE_GB) -> None:
    done = OSD_DATA_PATH / f"ceph-{i}" / "done"
    core_utils.exists_cache(done)(setup_osd)(i, size_gb)


def setup_osd(i: int, size_gb: int = OSD_SIZE_GB) -> Path:
    osd_dir = OSD_DATA_PATH / f"ceph-{i}"
    osd_dir.mkdir(parents=True, exist_ok=True)
    shutil.chown(osd_dir, "ceph", "ceph")

    backing_file = osd_dir / "backing.img"
    core_utils.run(
        "fallocate",
        ["--length", f"{size_gb}G", str(backing_file)],
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


def ensure_filesystem(name: str) -> None:
    filesystems = json.loads(core_utils.run("ceph", ["fs", "ls", "-f", "json"]))
    if not any(fs["name"] == name for fs in filesystems):
        metadata = ensure_pool(f"{name}-metadata")
        data = ensure_pool(f"{name}-data")
        core_utils.run("ceph", ["fs", "new", name, metadata, data])

    daemon = f"{name}.{core_utils.fqdn()}"
    directory = Path(f"/var/lib/ceph/mds/{CLUSTER}-{daemon}")
    directory.mkdir(parents=True, exist_ok=True)
    shutil.chown(directory, user="ceph", group="ceph")
    keyring = directory / "keyring"
    core_utils.run(
        "ceph",
        [
            "auth",
            "get-or-create",
            f"mds.{daemon}",
            "-o",
            str(keyring),
            "mon",
            "profile mds",
            "osd",
            "allow rwx",
            "mds",
            "allow",
        ],
    )
    shutil.chown(keyring, user="ceph", group="ceph")
    keyring.chmod(0o600)
    core_utils.run("ceph", ["config", "set", f"mds.{daemon}", "mds_join_fs", name])
    core_utils.run("systemctl", ["enable", "--now", f"ceph-mds@{daemon}"])

    # Workaround for slow CephFS initialization while new loop-backed OSDs
    # are peering and servicing the initial metadata writes in nested CI VMs.
    deadline = time.monotonic() + MDS_START_TIMEOUT
    while True:
        status = json.loads(
            core_utils.run("ceph", ["fs", "status", name, "-f", "json"])
        )
        if any(mds["state"] == "active" for mds in status["mdsmap"]):
            return
        if time.monotonic() >= deadline:
            raise RuntimeError(
                f"CephFS {name!r} has no active metadata server after "
                f"{MDS_START_TIMEOUT} seconds; last MDS state: {status['mdsmap']!r}"
            )
        time.sleep(2)


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


def _managed_mgr_folder() -> Path:
    return Path("/var/lib/ceph") / ceph_uuid() / f"mgr.{core_utils.fqdn()}"


def ensure_nfs_cluster(name: str) -> None:
    """Deploy Ceph-managed Ganesha using an upstream Manager and NFS image.

    MON, MDS and OSD daemons retain the installed Ubuntu packages. cephadm
    connects over SSH to this test VM to deploy the single NFS daemon.
    """
    version = core_utils.run("ceph", ["version"]).strip()
    match = re.match(r"ceph version ([0-9]+)\.[0-9]+\.[0-9]+(?:\s|[-+~]|$)", version)
    if match is None:
        raise RuntimeError(f"Cannot determine the Ceph image release from: {version!r}")
    image = f"quay.io/ceph/ceph:v{match.group(1)}"
    if not _managed_mgr_folder().exists():
        # Workaround for packaged Managers unable to load cephadm's PyO3
        # dependencies in Python subinterpreters (LP #2150762). Adopt only
        # the Manager into the upstream image; keep the storage daemons.
        # https://bugs.launchpad.net/ubuntu/+source/ceph/+bug/2150762
        core_utils.run(
            "cephadm",
            [
                "--image",
                image,
                "adopt",
                "--style",
                "legacy",
                "--name",
                f"mgr.{core_utils.fqdn()}",
            ],
        )
    # Adoption starts systemd asynchronously. Wait for the container's Manager
    # to register before querying its modules; the MON can still report the
    # retired package Manager briefly after its unit has stopped.
    deadline = time.monotonic() + 120
    while True:
        metadata = json.loads(core_utils.run("ceph", ["mgr", "metadata", "-f", "json"]))
        if any(
            m.get("name") == core_utils.fqdn() and m.get("container_image")
            for m in metadata
        ):
            break
        if time.monotonic() >= deadline:
            raise RuntimeError("The upstream Ceph Manager did not become ready")
        time.sleep(2)
    core_utils.run("ceph", ["mgr", "module", "enable", "cephadm"])
    core_utils.run("ceph", ["orch", "set", "backend", "cephadm"])
    core_utils.run("ceph", ["mgr", "module", "enable", "nfs"])
    core_utils.run("ceph", ["config", "set", "global", "container_image", image])
    core_utils.run("ceph", ["cephadm", "generate-key"])
    public_key = core_utils.run("ceph", ["cephadm", "get-pub-key"]).strip()
    authorized_keys = Path("/root/.ssh/authorized_keys")
    authorized_keys.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    existing = authorized_keys.read_text() if authorized_keys.exists() else ""
    if public_key not in existing.splitlines():
        separator = "\n" if existing and not existing.endswith("\n") else ""
        authorized_keys.write_text(existing + separator + public_key + "\n")
    authorized_keys.chmod(0o600)
    core_utils.enable_service("ssh")
    core_utils.restart_service("ssh")
    # cephadm checks the kernel hostname, which can differ from fqdn().
    hostname = socket.gethostname()
    core_utils.run("ceph", ["orch", "host", "add", hostname, core_utils.my_ip()])
    output = core_utils.run("ceph", ["nfs", "cluster", "ls", "-f", "json"]).strip()
    # Quincy returns newline-separated IDs even with --format=json.
    clusters = json.loads(output) if output.startswith("[") else output.splitlines()
    if name not in clusters:
        core_utils.run("ceph", ["nfs", "cluster", "create", name, hostname])

    deadline = time.monotonic() + 600
    while True:
        daemons = json.loads(
            core_utils.run(
                "ceph",
                ["orch", "ps", "--daemon_type", "nfs", "--refresh", "-f", "json"],
            )
        )
        ours = [d for d in daemons if d["daemon_name"].startswith(f"nfs.{name}.")]
        if ours and all(d.get("status_desc") == "running" for d in ours):
            info = json.loads(
                core_utils.run("ceph", ["nfs", "cluster", "info", name, "-f", "json"])
            )
            if info.get(name, {}).get("backend"):
                return
        if time.monotonic() >= deadline:
            raise RuntimeError(f"Ceph NFS cluster {name!r} is not ready: {ours!r}")
        time.sleep(5)
