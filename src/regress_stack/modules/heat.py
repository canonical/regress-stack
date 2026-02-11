# Copyright 2025 - Canonical Ltd
# SPDX-License-Identifier: GPL-3.0-only

import logging
import pathlib

from regress_stack.core import apt as core_apt
from regress_stack.core import utils as core_utils
from regress_stack.modules import keystone, mysql, neutron, nova, rabbitmq
from regress_stack.modules import utils as module_utils

LOG = logging.getLogger(__name__)

DEPENDENCIES = {keystone, mysql, rabbitmq, nova, neutron}
PACKAGES = ["heat-api", "heat-api-cfn", "heat-engine"]
LOGS = ["/var/log/heat/"]

CONF = "/etc/heat/heat.conf"
URL = f"http://{core_utils.my_ip()}:8004"
URL_CFN = URL + "/v1"
URL_ORCHESTRATION = URL_CFN + "/%(tenant_id)s"
SERVICE = "heat"
SERVICE_CFN = "heat-cfn"
SERVICE_TYPE = "orchestration"
SERVICE_TYPE_CFN = "cloudformation"
HEAT_FLAMINGO_VERSION = "1:25.0.0~rc1-0ubuntu3"
HEAT_STACK_ADMIN = "heat_admin"
HEAT_STACK_ADMIN_PASSWORD = "changeme"

HEAT_STACK_OWNER = "heat_stack_owner"
HEAT_STACK_USER = "heat_stack_user"

URL_HEAT_METADATA = f"http://{core_utils.my_ip()}:8000"
URL_HEAT_METADATA_WAIT = URL_HEAT_METADATA + "/v1/waitcondition"


# tempest run --list --regex heat_tempest_plugin.tests.functional.test_nova_server_networks --regex '^(.(?!(test_create_update_server_add_subnet)))*$' --regex '^(.(?!(test_create_stack_with_multi_signal_waitcondition)))*$' --regex '^(.(?!(aodh)))*$ --regex ^(.(?!(test_extra_route_set)))*$'
TEST_INCLUDE_REGEXES = [
    r"heat_tempest_plugin.tests.functional.test_nova_server_networks",
]

TEST_EXCLUDE_REGEXES = [
    "test_create_update_server_add_subnet",  # fails with stack already exists
    "test_create_stack_with_multi_signal_waitcondition",  # failure to investigate
    "aodh",  # not yet implemented
    "test_extra_route_set",  # deprecated?
]


def setup():
    db_user, db_pass = mysql.ensure_service(SERVICE)
    rabbit_user, rabbit_pass = rabbitmq.ensure_service(SERVICE)
    username, password = keystone.ensure_service_account(
        SERVICE, SERVICE_TYPE, URL_ORCHESTRATION
    )
    service_cfn = keystone.ensure_service(SERVICE_CFN, SERVICE_TYPE_CFN)
    keystone.ensure_endpoint(service_cfn, URL_CFN)
    domain = keystone.ensure_domain(SERVICE)
    heat_stack_admin = keystone.ensure_user(
        HEAT_STACK_ADMIN, HEAT_STACK_ADMIN_PASSWORD, domain.id
    )
    keystone.grant_domain_role(heat_stack_admin, keystone.admin_role(), domain)
    keystone.ensure_role(HEAT_STACK_OWNER)
    keystone.ensure_role(HEAT_STACK_USER)
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
            "trustee",
            {
                "auth_type": "password",
                "auth_url": keystone.OS_AUTH_URL,
                "username": username,
                "password": password,
                "user_domain_id": keystone.service_domain(),
            },
        ),
        ("DEFAULT", "num_engine_workers", "1"),
        ("heat_api", "workers", "1"),
        ("heat_api_cfn", "workers", "1"),
        ("DEFAULT", "transport_url", rabbitmq.transport_url(rabbit_user, rabbit_pass)),
        ("DEFAULT", "heat_metadata_server_url", URL_HEAT_METADATA),
        ("DEFAULT", "heat_waitcondition_server_url", URL_HEAT_METADATA_WAIT),
        ("DEFAULT", "instance_driver", "heat.engine.nova"),
        *module_utils.dict_to_cfg_set_args(
            "DEFAULT",
            {
                "stack_user_domain_id": domain.id,
                "stack_domain_admin": HEAT_STACK_ADMIN,
                "stack_domain_admin_password": HEAT_STACK_ADMIN_PASSWORD,
            },
        ),
    )
    core_utils.sudo("heat-manage", ["db_sync"], user=SERVICE)
    heat_daemons = ["heat-api", "heat-api-cfn", "heat-engine"]
    if core_apt.PkgVersionCompare("python3-heat") >= HEAT_FLAMINGO_VERSION:
        heat_daemons.remove("heat-api")
        heat_daemons.remove("heat-api-cfn")
        # heat-api and heat-api-cfn run as WSGI apps under apache2.
        heat_daemons.insert(0, "apache2")

    for _daemon in heat_daemons:
        core_utils.restart_service(_daemon)


def configure_tempest(tempest_conf: pathlib.Path):
    """Configure tempest for heat."""
    conf = str(tempest_conf)

    demo_project = keystone.ensure_project(
        "heat-demo-project", keystone.default_domain()
    )
    keystone.grant_project_role(
        keystone.admin_user(), keystone.admin_role(), demo_project
    )

    heat_demo_network = neutron.ensure_network("heat-demo-network", demo_project.id)
    heat_demo_subnet = neutron.ensure_subnet(
        "heat-demo-subnet", heat_demo_network, "192.168.0.0/24"
    )
    heat_demo_router = neutron.ensure_router("heat-demo-router", demo_project)
    neutron.ensure_subnet_router(heat_demo_subnet, heat_demo_router)

    module_utils.cfg_set(
        conf,
        *module_utils.dict_to_cfg_set_args(
            "heat_plugin",
            {
                "network_for_ssh": neutron.EXTERNAL_NETWORK,
                "fixed_network_name": heat_demo_network.name,
                "floating_network_name": neutron.EXTERNAL_NETWORK,
                "image_ssh_user": "ubuntu",
                "project_name": demo_project.name,
            },
        ),
    )
