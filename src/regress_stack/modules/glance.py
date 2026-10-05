# Copyright 2025 - Canonical Ltd
# SPDX-License-Identifier: GPL-3.0-only

import logging
import pathlib

from regress_stack.core import apt as core_apt
from regress_stack.core import utils as core_utils
from regress_stack.modules import barbican, keystone, mysql
from regress_stack.modules import utils as module_utils

LOG = logging.getLogger(__name__)

DEPENDENCIES = {keystone, mysql}
OPTIONAL_DEPENDENCIES = {barbican}
PACKAGES = ["glance-api"]
LOGS = ["/var/log/glance/"]

CONF = "/etc/glance/glance-api.conf"
URL = f"http://{core_utils.my_ip()}:9292/"
SERVICE = "glance"
SERVICE_TYPE = "image"
GLANCE_STRICT_IMAGE_FORMAT_VERSION = "31.0.0"


def _disable_strict_image_format_validation():
    if core_apt.PkgVersionCompare("python3-glance", upstream=True) < (
        GLANCE_STRICT_IMAGE_FORMAT_VERSION
    ):
        return
    core_utils.warn_workaround(
        "glance image upload validation",
        "strict format matching rejects Tempest's legacy upload smoke on newer "
        "Glance; disabling require_image_format_match locally until the "
        "package/test path is reconciled",
    )
    module_utils.cfg_set(CONF, ("image_format", "require_image_format_match", "false"))


def _ensure_staging_dir():
    staging = pathlib.Path("/var/lib/glance/staging")
    staging.mkdir(parents=True, exist_ok=True)
    core_utils.run("chown", ["glance:glance", str(staging)])


def setup():
    db_user, db_pass = mysql.ensure_service(SERVICE)
    username, password = keystone.ensure_service_account(SERVICE, SERVICE_TYPE, URL)
    module_utils.cfg_set(
        CONF,
        (
            "database",
            "connection",
            mysql.connection_string(SERVICE, db_user, db_pass),
        ),
        ("database", "max_pool_size", "1"),
        ("paste_deploy", "flavor", "keystone"),
        *module_utils.dict_to_cfg_set_args(
            "keystone_authtoken", keystone.authtoken_service(username, password)
        ),
        ("DEFAULT", "workers", "1"),
        ("DEFAULT", "enabled_backends", "fs:file"),
        ("glance_store", "default_backend", "fs"),
        ("fs", "filesystem_store_datadir", "/var/lib/glance/images/"),
        # Staging store required for the image import flow used by
        # openstacksdk create_image(wait=True).
        (
            "os_glance_staging_store",
            "filesystem_store_datadir",
            "/var/lib/glance/staging",
        ),
    )

    _ensure_staging_dir()
    _disable_strict_image_format_validation()

    if barbican.installed():
        module_utils.cfg_set(
            CONF,
            ("key_manager", "backend", "barbican"),
            *module_utils.dict_to_cfg_set_args(
                "key_manager", barbican.key_manager_cfg()
            ),
        )
    core_utils.sudo("glance-manage", ["db_sync"], user=SERVICE)
    # Hibiscus glance (>= 33.0.0) serves the API via apache2 instead of a
    # standalone glance-api unit. Reload apache2 there as a full restart would
    # bounce other WSGI services, otherwise restart the unit.
    if core_utils.has_service("glance-api"):
        core_utils.restart_service("glance-api")
        LOG.info("Using standalone glance-api unit")
    else:
        core_utils.graceful_apache()
        LOG.info("Using apache2 WSGI for glance-api")


def ensure_image(name: str, filepath: pathlib.Path, **kwargs):
    conn = keystone.o7k()

    image = conn.image.find_image(name, ignore_missing=True)
    if image:
        return image
    return conn.image.create_image(
        name=name, filename=str(filepath), wait=True, **kwargs
    )
