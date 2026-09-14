# Copyright 2025 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

import copy
import logging
import os
import pathlib
import typing

from regress_stack.core import apt as core_apt
from regress_stack.core import utils as core_utils
from regress_stack.modules import keystone, mysql, rabbitmq
from regress_stack.modules import utils as module_utils

LOG = logging.getLogger(__name__)

DEPENDENCIES = {keystone, mysql, rabbitmq}
_BASE_PACKAGES = [
    "barbican-api",
    "barbican-keystone-listener",
    "barbican-worker",
]
LOGS = ["/var/log/barbican"]

CONF = "/etc/barbican/barbican.conf"
URL = f"http://{core_utils.my_ip()}:9311/"
SERVICE = "barbican"
SERVICE_TYPE = "key-manager"
BARBICAN_ROLES = [
    "admin",
    "creator",
    "key-manager:service-admin",
    "member",
    "reader",
]
# Fernet key for the simple_crypto plugin (base64url-encoded 32 bytes).
# Required for secret storage; without it barbican returns 500 on any
# secret with a payload.
SIMPLE_CRYPTO_KEK = "YWJjZGVmZ2hpamtsbW5vcHFyc3R1dnd4eXowMTIzNDU="

TEST_INCLUDE_REGEXES = [
    "barbican_tempest_plugin.tests.api",
    "barbican_tempest_plugin.tests.rbac",
    "barbican_tempest_plugin.tests.scenario",
]
TEST_EXCLUDE_REGEXES = [
    # barbican-tempest-plugin < 4.0.0 scenario tests and some API tests
    # fail on Noble and older releases because:
    #
    # 1. QuotasTest.test_get_effective_quota expects a 403 Forbidden when
    #    scope enforcement is enabled, but our RBAC relaxation (which is
    #    needed for the old plugin's scenario tests) disables enforcement,
    #    causing the quota endpoint to return 200 OK instead of 403.
    #
    # 2. CVE20223100Test and all scenario tests call secret_client.
    #    create_secret() (POST /v1/secrets) during setUp(). Even with
    #    enforce_scope=false and enforce_new_defaults=false, barbican's
    #    default policy requires the "creator" role. The tempest "primary"
    #    user only has "member", so these tests get 403 Forbidden.
    #
    # These tests are expected to pass once barbican-tempest-plugin >= 4.0.0
    # is the only version in the archive (removing the need for RBAC
    # relaxation and the old plugin's creator-role requirement).
    "barbican_tempest_plugin.tests.api.test_quotas.QuotasTest.test_get_effective_quota",
    "barbican_tempest_plugin.tests.api.test_cve_2022_3100.CVE20223100Test.test_cve_2022_3100",
    "barbican_tempest_plugin.tests.scenario.test_certificate_validation.CertificateValidationTest.test_signed_image_upload_and_boot",
    "barbican_tempest_plugin.tests.scenario.test_certificate_validation.CertificateValidationTest.test_signed_image_invalid_cert_boot_failure",
    "barbican_tempest_plugin.tests.scenario.test_certificate_validation.CertificateValidationTest.test_signed_image_upload_and_hard_reboot",
    "barbican_tempest_plugin.tests.scenario.test_certificate_validation.CertificateValidationTest.test_signed_image_upload_and_server_rebuild",
    "barbican_tempest_plugin.tests.scenario.test_ephemeral_disk_encryption.EphemeralStorageEncryptionTest.test_encrypted_ephemeral_lvm_storage",
    "barbican_tempest_plugin.tests.scenario.test_image_signing.ImageSigningTest.test_signed_image_upload_and_boot",
    "barbican_tempest_plugin.tests.scenario.test_image_signing.ImageSigningSnapshotTest.test_signed_image_upload_boot_snapshot",
    "barbican_tempest_plugin.tests.scenario.test_volume_encryption.VolumeEncryptionTest.test_encrypted_cinder_volumes_cryptsetup",
    "barbican_tempest_plugin.tests.scenario.test_volume_encryption.VolumeEncryptionTest.test_encrypted_cinder_volumes_luks",
]

# Tempest >= 26.0.0 removed CONF.scenario.img_dir from its config schema,
# but barbican-tempest-plugin < 4.0.0 still references it in
# barbican_manager.py setUp() as a fallback when img_file is not a full
# path. When both conditions are met we register img_dir manually and split
# the path so the old plugin can resolve the image location.
BARBICAN_PLUGIN_PKG = "barbican-tempest-plugin"
BARBICAN_PLUGIN_NO_IMG_DIR = "4.0.0"
TEMPEST_NO_IMG_DIR = (26, 0, 0)


def determine_packages(no_tempest: bool = False) -> list[str]:
    """Determine the packages to install for this module."""

    packages = copy.deepcopy(_BASE_PACKAGES)
    if not no_tempest:
        packages.append(BARBICAN_PLUGIN_PKG)

    return packages


def setup():
    db_user, db_pass = mysql.ensure_service(SERVICE)
    rabbit_user, rabbit_pass = rabbitmq.ensure_service(SERVICE)
    username, password = keystone.ensure_service_account(SERVICE, SERVICE_TYPE, URL)
    for role in BARBICAN_ROLES:
        keystone.ensure_role(role)
    module_utils.cfg_set(
        CONF,
        # Barbican on Jammy (Yoga) reads the DB connection from
        # [DEFAULT].sql_connection, while Noble (Caracal) and newer
        # read from [database].connection. Set both for compatibility.
        (
            "DEFAULT",
            "sql_connection",
            mysql.connection_string(SERVICE, db_user, db_pass),
        ),
        (
            "database",
            "connection",
            mysql.connection_string(SERVICE, db_user, db_pass),
        ),
        ("database", "max_pool_size", "1"),
        *module_utils.dict_to_cfg_set_args(
            "keystone_authtoken", keystone.authtoken_service(username, password)
        ),
        *module_utils.dict_to_cfg_set_args(
            "service_auth", keystone.account_dict(username, password)
        ),
        ("DEFAULT", "transport_url", rabbitmq.transport_url(rabbit_user, rabbit_pass)),
        # barbican-tempest-plugin < 4.0.0 was written before scope-
        # enforced RBAC existed. Its scenario tests use a tempest
        # "primary" user that lacks the "creator" role, and the quota
        # API test expects unscoped admin access. Disable both
        # enforce_scope and enforce_new_defaults so these tests can
        # create secrets and read quotas without 403 Forbidden errors.
        (
            "oslo_policy",
            "enforce_scope",
            "false" if _barbican_plugin_needs_img_dir() else "true",
        ),
        (
            "oslo_policy",
            "enforce_new_defaults",
            "false" if _barbican_plugin_needs_img_dir() else "true",
        ),
        (
            "simple_crypto_plugin",
            "kek",
            module_utils.preseed_value("barbican/kek", SIMPLE_CRYPTO_KEK),
        ),
    )
    module_utils.bootstrap_sudo(
        "barbican-manage",
        ["--config-file", CONF, "db", "upgrade"],
        user=SERVICE,
    )
    core_utils.restart_service("barbican-keystone-listener", "barbican-worker")


def installed() -> bool:
    return core_apt.pkgs_installed(_BASE_PACKAGES)


def key_manager_cfg() -> typing.Dict[str, str]:
    username, password = keystone.ensure_service_account(SERVICE, SERVICE_TYPE, URL)
    cfg = keystone.account_dict(username, password)
    cfg["backend"] = "barbican"
    return cfg


def configure_tempest(tempest_conf: pathlib.Path):
    """Configure tempest for barbican."""
    conf = str(tempest_conf)
    module_utils.cfg_set(
        conf,
        ("service_available", "barbican", "True"),
        # The enforce_scope flag moved between plugin versions:
        # Jammy (v1.5.0) reads [barbican_rbac_scope_verification].enforce_scope,
        # Noble+ (v4.5.0) reads [enforce_scope].barbican. Set both so
        # ProjectQuotasTest skips correctly on all supported releases.
        # barbican-tempest-plugin < 4.0.0 was written before scope-
        # enforced RBAC existed; disabling enforce_scope tells tempest
        # not to expect scoped responses, so the old quota test can run.
        (
            "enforce_scope",
            "barbican",
            "False" if _barbican_plugin_needs_img_dir() else "True",
        ),
        ("barbican_rbac_scope_verification", "enforce_scope", "True"),
        *module_utils.dict_to_cfg_set_args(
            "key_manager",
            {
                "region": module_utils.REGION,
                "max_microversion": "1.1",
            },
        ),
        ("image_signature_verification", "enforced", "False"),
        ("image_signature_verification", "certificate_validation", "False"),
        ("ephemeral_storage_encryption", "enabled", "True"),
        ("compute_feature_enabled", "attach_encrypted_volume", "True"),
    )
    _configure_tempest_image(tempest_conf)


def _configure_tempest_image(tempest_conf: pathlib.Path):
    """Ensure the scenario image path is resolvable by barbican-tempest-plugin.

    Tempest >= 26.0.0 removed CONF.scenario.img_dir from its config schema.
    barbican-tempest-plugin < 4.0.0 still references img_dir as a fallback
    in its setUp() method. When both are true, we register the option at
    Python startup via sitecustomize.py and split the path into img_dir +
    img_file so the old plugin can compose the full path.
    """
    conf = str(tempest_conf)
    img_file = module_utils.cfg_get(conf, "scenario", "img_file").strip()
    if not img_file:
        return
    if os.path.isabs(img_file):
        return

    workspace = tempest_conf.parent.parent.resolve()
    full_path = str(workspace / img_file)
    img_dir, img_filename = os.path.split(full_path)

    tv = core_utils.tempest_version()
    if tv is None:
        LOG.warning(
            "Could not determine tempest version, assuming img_dir is available"
        )

    need_img_dir = (
        tv is not None and tv >= TEMPEST_NO_IMG_DIR and _barbican_plugin_needs_img_dir()
    )
    if need_img_dir:
        _write_sitecustomize(workspace)
        module_utils.cfg_set(
            conf,
            ("scenario", "img_dir", img_dir),
            ("scenario", "img_file", img_filename),
        )
    else:
        module_utils.cfg_set(conf, ("scenario", "img_file", full_path))


def _barbican_plugin_needs_img_dir() -> bool:
    """Return True if barbican-tempest-plugin < 4.0.0 is installed.

    Uses candidate=True + upstream=True so the version comparison works
    even when the package is not yet installed.
    """
    try:
        return (
            core_apt.PkgVersionCompare(
                BARBICAN_PLUGIN_PKG, candidate=True, upstream=True
            )
            < BARBICAN_PLUGIN_NO_IMG_DIR
        )
    except ValueError:
        LOG.warning(
            "Could not determine %s version, assuming modern",
            BARBICAN_PLUGIN_PKG,
        )
        return False


def _write_sitecustomize(workspace: pathlib.Path):
    """Write a sitecustomize.py that registers the removed img_dir option.

    sitecustomize.py is loaded automatically by Python's site module at
    interpreter startup. This file registers the img_dir option that was
    removed from tempest >= 26.0.0 but is still referenced by
    barbican-tempest-plugin < 4.0.0.
    """
    path = workspace / "sitecustomize.py"
    path.write_text(
        "# Auto-generated by regress-stack. Registers CONF.scenario.img_dir\n"
        "# which was removed from tempest >= 26.0.0 but is still referenced\n"
        "# by barbican-tempest-plugin < 4.0.0.\n"
        "from oslo_config import cfg\n"
        "from tempest import config as tempest_config\n"
        "tempest_config.CONF.register_opt(\n"
        "    cfg.StrOpt('img_dir', default=''), group='scenario')\n"
    )
