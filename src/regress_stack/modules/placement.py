# Copyright 2025 - Canonical Ltd
# SPDX-License-Identifier: GPL-3.0-only

import logging

from regress_stack.core import utils as core_utils
from regress_stack.modules import keystone, mysql
from regress_stack.modules import utils as module_utils

LOG = logging.getLogger(__name__)

DEPENDENCIES = {keystone, mysql}
PACKAGES = ["placement-api"]
LOGS = [
    "/var/log/apache2/placement_api_access.log",
    "/var/log/apache2/placement_api_error.log",
]

CONF = "/etc/placement/placement.conf"
URL = f"http://{core_utils.my_ip()}:8778/"


def setup():
    db_user, db_pass = mysql.ensure_service("placement")
    username, password = keystone.ensure_service_account("placement", "placement", URL)
    core_utils.run(
        "sed",
        [
            "-i",
            "s|placement-api processes=5 threads=1|placement-api processes=1 threads=1|",
            "/etc/apache2/sites-enabled/placement-api.conf",
        ],
    )
    module_utils.cfg_set(
        CONF,
        (
            "placement_database",
            "connection",
            mysql.connection_string("placement", db_user, db_pass),
        ),
        ("placement_database", "max_pool_size", "1"),
        ("api", "auth_strategy", "keystone"),
        *module_utils.dict_to_cfg_set_args(
            "keystone_authtoken", keystone.authtoken_service(username, password)
        ),
    )
    core_utils.sudo("placement-manage", ["db", "sync"], user="placement")
    core_utils.restart_apache()
