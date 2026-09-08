# Copyright 2026 - Canonical Ltd
# SPDX-License-Identifier: GPL-3.0-only

import json
from pathlib import Path
import shutil
import uuid

from regress_stack.multinode import common


CAPABILITIES = {
    "mon.": {"mon": "allow *"},
    "client.admin": {
        "mon": "allow *",
        "osd": "allow *",
        "mgr": "allow *",
        "mds": "allow *",
    },
    "client.images": {
        "mon": "profile rbd",
        "osd": "profile rbd pool=images",
        "mgr": "profile rbd pool=images",
    },
    "client.volumes": {
        "mon": "profile rbd",
        "osd": "profile rbd pool=volumes, profile rbd-read-only pool=images",
        "mgr": "profile rbd pool=volumes",
    },
}


def keyring(name, key, caps=None):
    output = f"[{name}]\n    key = {key}\n"
    for service, cap in (caps or {}).items():
        output += f'    caps {service} = "{cap}"\n'
    return output


def configuration(context):
    nodes = context.deployment.controllers
    size = len(nodes)
    return f"""[global]
fsid = {context.secret("ceph/fsid")}
mon host = {",".join(node.address for node in nodes)}
public network = {context.deployment.management_cidr}
auth cluster required = cephx
auth service required = cephx
auth client required = cephx
auth allow insecure global_id reclaim = false
osd pool default size = {size}
osd pool default min size = {2 if size == 3 else 1}
osd crush chooseleaf type = 1
"""


def setup():
    context = common.context()
    common.write("/etc/ceph/ceph.conf", configuration(context), mode=0o644)
    for pool, user in (("volumes", "nova"), ("images", "glance")):
        if pool == "images" and not context.controller:
            continue
        # cinder and nova both consume the volumes keyring; grant only their
        # shared ceph group access, instead of exposing the key to all users.
        path = Path(f"/etc/ceph/ceph.client.{pool}.keyring")
        common.write(
            path,
            keyring(f"client.{pool}", context.secret(f"ceph/client.{pool}")),
            user=user,
        )
        if context.controller and pool == "volumes":
            common.run("usermod", ["-a", "-G", "ceph", "nova"])
            common.run("usermod", ["-a", "-G", "ceph", "cinder"])
            shutil.chown(path, user="root", group="ceph")
            path.chmod(0o640)
    if not context.controller:
        return
    common.write(
        "/etc/ceph/ceph.client.admin.keyring",
        keyring("client.admin", context.secret("ceph/client.admin")),
    )
    name = context.local.name
    if not common.done("ceph-mon"):
        mon_dir = Path(f"/var/lib/ceph/mon/ceph-{name}")
        mon_dir.mkdir(parents=True, exist_ok=True)
        shutil.chown(mon_dir, user="ceph", group="ceph")
        monmap = common.STATE / "monmap"
        mon_keyring = common.STATE / "mon.keyring"
        content = keyring("mon.", context.secret("ceph/mon."), CAPABILITIES["mon."])
        if context.bootstrap:
            for entity, caps in CAPABILITIES.items():
                if entity != "mon.":
                    content += keyring(entity, context.secret(f"ceph/{entity}"), caps)
            for node in context.deployment.controllers:
                content += keyring(
                    f"mgr.{node.name}",
                    context.secret(f"ceph/mgr.{node.name}"),
                    {"mon": "profile mgr", "osd": "allow *", "mds": "allow *"},
                )
            common.run(
                "monmaptool",
                [
                    "--create",
                    "--clobber",
                    "--add",
                    name,
                    context.local.address,
                    "--fsid",
                    context.secret("ceph/fsid"),
                    str(monmap),
                ],
            )
        else:
            common.run("ceph", ["mon", "getmap", "-o", str(monmap)])
        common.write(mon_keyring, content)
        common.run(
            "ceph-mon",
            [
                "--mkfs",
                "-i",
                name,
                "--monmap",
                str(monmap),
                "--keyring",
                str(mon_keyring),
            ],
        )
        common.run("chown", ["-R", "ceph:ceph", str(mon_dir)])
        common.restart(f"ceph-mon@{name}")
        common.wait_for(
            lambda: name
            in json.loads(common.run("ceph", ["quorum_status", "--format", "json"]))[
                "quorum_names"
            ],
            "local Ceph monitor",
            timeout=180,
        )
        common.mark("ceph-mon")
    if context.bootstrap:
        common.run("ceph", ["mon", "enable-msgr2"])
    if not common.done("ceph-mgr"):
        manager = Path(f"/var/lib/ceph/mgr/ceph-{name}")
        manager.mkdir(parents=True, exist_ok=True)
        shutil.chown(manager, user="ceph", group="ceph")
        common.write(
            manager / "keyring",
            keyring(f"mgr.{name}", context.secret(f"ceph/mgr.{name}")),
            user="ceph",
        )
        common.restart(f"ceph-mgr@{name}")
        common.mark("ceph-mgr")
    for slot in range(3):
        setup_osd(slot)
    if context.bootstrap:
        pools = common.run("ceph", ["osd", "pool", "ls"]).splitlines()
        for pool in ("images", "volumes"):
            if pool not in pools:
                common.run("ceph", ["osd", "pool", "create", pool, "32"])
                common.run(
                    "ceph", ["osd", "pool", "application", "enable", pool, "rbd"]
                )


def setup_osd(slot):
    if common.done(f"ceph-osd-{slot}"):
        return
    record = common.STATE / f"osd-{slot}.json"
    from regress_stack.core.deployment import private_read

    if record.exists():
        identity = json.loads(private_read(record))
    else:
        identity = {
            "uuid": str(uuid.uuid4()),
            "key": common.run("ceph-authtool", ["--gen-print-key"]).strip(),
        }
        common.write(record, json.dumps(identity))
    secret = json.dumps({"cephx_secret": identity["key"]})
    osd_id = common.run(
        "ceph", ["osd", "new", identity["uuid"], "-i", "-"], input=secret
    ).strip()
    if not osd_id.isdigit():
        raise RuntimeError("Ceph returned an invalid OSD ID")
    directory = Path(f"/var/lib/ceph/osd/ceph-{osd_id}")
    directory.mkdir(parents=True, exist_ok=True)
    backing = directory / "backing.img"
    if not backing.exists():
        common.run("fallocate", ["--length", "10G", str(backing)])
    common.write(
        directory / "keyring", keyring(f"osd.{osd_id}", identity["key"]), user="ceph"
    )
    common.run("chown", ["-R", "ceph:ceph", str(directory)])
    if not (directory / "ready").exists():
        common.run(
            "sudo",
            [
                "-u",
                "ceph",
                "ceph-osd",
                "--mkfs",
                "-i",
                osd_id,
                "--osd-uuid",
                identity["uuid"],
                "--no-mon-config",
                "--bluestore-block-path",
                str(backing),
            ],
            timeout=180,
        )
    common.restart(f"ceph-osd@{osd_id}")
    common.mark(f"ceph-osd-{slot}")
