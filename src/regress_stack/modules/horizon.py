# Copyright 2026 - Canonical Ltd
# SPDX-License-Identifier: GPL-3.0-only

import logging
import pathlib
import re

from regress_stack.core import utils as core_utils
from regress_stack.modules import keystone

LOG = logging.getLogger(__name__)

DEPENDENCIES = {keystone}
PACKAGES = ["openstack-dashboard", "memcached"]
LOGS = ["/var/log/apache2/"]

LOCAL_SETTINGS = pathlib.Path("/etc/openstack-dashboard/local_settings.py")
DASHBOARD_CONF = pathlib.Path("/etc/apache2/conf-available/openstack-dashboard.conf")


def _upsert_setting(contents: str, key: str, value: str) -> str:
    """Update or insert the value of a setting key in the given contents."""
    line = f"{key} = {value}"
    pattern = re.compile(rf"^\s*{re.escape(key)}\s*=.*$", re.MULTILINE)
    if pattern.search(contents):
        return pattern.sub(line, contents, count=1)

    suffix = "" if contents.endswith("\n") else "\n"
    return f"{contents}{suffix}{line}\n"


def _allowed_hosts() -> list[str]:
    hosts = ["localhost", "127.0.0.1", core_utils.my_ip(), core_utils.fqdn()]
    # Preserve order while removing duplicates.
    return list(dict.fromkeys(hosts))


def setup():
    if not LOCAL_SETTINGS.exists():
        raise RuntimeError(f"Expected Horizon settings file missing: {LOCAL_SETTINGS}")

    contents = LOCAL_SETTINGS.read_text()

    ip = core_utils.my_ip()

    new_contents = _upsert_setting(contents, "OPENSTACK_HOST", repr(ip))

    new_contents = _upsert_setting(
        new_contents,
        "OPENSTACK_KEYSTONE_URL",
        repr(f"http://{ip}:5000/v3/"),
    )

    new_contents = _upsert_setting(
        new_contents,
        "OPENSTACK_KEYSTONE_DEFAULT_DOMAIN",
        repr("Default"),
    )

    new_contents = _upsert_setting(
        new_contents,
        "ALLOWED_HOSTS",
        repr(_allowed_hosts()),
    )

    if new_contents != contents:
        LOCAL_SETTINGS.write_text(new_contents)
        LOG.info("Updated Horizon settings in %s", LOCAL_SETTINGS)

    if not DASHBOARD_CONF.exists():
        raise RuntimeError(f"Horizon Apache config missing: {DASHBOARD_CONF}")

    # Update the WSGI worker configuration
    core_utils.run(
        "sed",
        [
            "-i",
            "s|processes=3 threads=10|processes=1 threads=1|",
            str(DASHBOARD_CONF),
        ],
    )

    core_utils.restart_apache()
