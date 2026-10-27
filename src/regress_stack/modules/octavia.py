# Copyright 2025 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

import logging
import pathlib
import shutil

from regress_stack.core import utils as core_utils
from regress_stack.modules import keystone, mysql, neutron, nova, ovn, rabbitmq
from regress_stack.modules import utils as module_utils

LOG = logging.getLogger(__name__)

DEPENDENCIES = {keystone, mysql, rabbitmq, ovn, nova, neutron}
PACKAGES = [
    "octavia-api",
    "octavia-housekeeping",
    "octavia-worker",
    "octavia-driver-agent",
    "python3-ovn-octavia-provider",
]
LOGS = ["/var/log/octavia/"]

CONF = "/etc/octavia/octavia.conf"
URL = f"http://{core_utils.my_ip()}:9876/"
SERVICE = "octavia"
SERVICE_TYPE = "load-balancer"
OCTAVIA_ROLES = (
    "load-balancer_admin",
    "load-balancer_observer",
    "load-balancer_global_observer",
    "load-balancer_member",
    "load-balancer_admin",
)
SOCKET_DIR = "/var/run/octavia"


TEST_INCLUDE_REGEXES = [
    r"octavia_tempest_plugin.tests.scenario.*SIP.*",
    r"octavia_tempest_plugin.tests.scenario.*source_ip_port.*",
]

TEST_EXCLUDE_REGEXES = [
    # None of the following tests are supported by the ovn provider
    r"PROXY",
    r"HTTP",
    r"http",
    r"mixed",
    r"_RR_",
    r"_SI_",
    r"_LC_",
    r"L7",
    r"ListenerScenarioTest",
    # Tries to configure an interface called eth0 on spawned VM, but does not exist
    r"octavia_tempest_plugin.tests.scenario.v2.test_traffic_ops.*",
    r"octavia_tempest_plugin.tests.scenario.v2.test_ipv6_traffic_ops.*",
]


def setup():
    db_user, db_pass = mysql.ensure_service(SERVICE)
    rabbit_user, rabbit_pass = rabbitmq.ensure_service(SERVICE)
    username, password = keystone.ensure_service_account(SERVICE, SERVICE_TYPE, URL)
    for role in OCTAVIA_ROLES:
        keystone.ensure_role(role)
    socket_dir = pathlib.Path(SOCKET_DIR)
    socket_dir.mkdir(parents=True, exist_ok=True)
    shutil.chown(socket_dir, SERVICE, SERVICE)
    module_utils.cfg_set(
        CONF,
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
        ("oslo_messaging", "topic", "octavia_prov"),
        ("api_settings", "bind_host", "0.0.0.0"),
        ("api_settings", "enabled_provider_drivers", "ovn:Octavia OVN driver"),
        ("api_settings", "default_provider_driver", "ovn"),
        ("driver_agent", "enabled_provider_agents", "ovn"),
        *module_utils.dict_to_cfg_set_args(
            "ovn",
            {
                "ovn_nb_connection": ovn.OVNNB_CONNECTION,
                "ovn_sb_connection": ovn.OVNSB_CONNECTION,
            },
        ),
    )
    core_utils.sudo("octavia-db-manage", ["upgrade", "head"], user=SERVICE)
    core_utils.restart_service(
        "octavia-driver-agent", "octavia-worker", "octavia-api", "octavia-housekeeping"
    )


def configure_tempest(tempest_conf: pathlib.Path):
    """Configure tempest for Octavia."""
    module_utils.cfg_set(
        str(tempest_conf),
        *module_utils.dict_to_cfg_set_args(
            "load_balancer",
            {
                "member_role": "load-balancer_member",
                "admin_role": "load-balancer_admin",
                "observer_role": "load-balancer_observer",
                "global_observer_role": "load-balancer_global_observer",
                "RBAC_test_type": "keystone_default_roles",
                "enabled_provider_drivers": "ovn:Octavia OVN driver",
                "provider": "ovn",
            },
        ),
        *module_utils.dict_to_cfg_set_args(
            "loadbalancer-feature-enabled",
            {
                "health_monitor_enabled": "true",
                "l7_protocol_enabled": "false",
                "l4_protocol": "TCP",
                "session_persistence_enabled": "false",
                "pool_algorithms_enabled": "false",
                "quotas_enabled": "false",
                "not_implemented_is_error": "false",
            },
        ),
    )
