# Copyright 2025 - Canonical Ltd
# SPDX-License-Identifier: GPL-3.0-only

import copy
import functools
import ipaddress
import logging
import time

from regress_stack import topology
from regress_stack.core import apt as core_apt
from regress_stack.core import quiet as core_quiet
from regress_stack.core import utils as core_utils
from regress_stack.modules import keystone, mysql, ovn, rabbitmq
from regress_stack.modules import utils as module_utils
from regress_stack.roles import Role

LOG = logging.getLogger(__name__)

DEPENDENCIES = {keystone, mysql, ovn, rabbitmq}
_BASE_PACKAGES = ["neutron-ovn-metadata-agent"]

LOGS = ["/var/log/neutron/"]

CONF = "/etc/neutron/neutron.conf"
METADATA_AGENT_CONF = "/etc/neutron/neutron_ovn_metadata_agent.ini"
ML2_CONF = "/etc/neutron/plugins/ml2/ml2_conf.ini"

METADATA_SECRET = "bonjour"

EXTERNAL_NETWORK = "external-network"

NEUTRON_SPLIT_SERVICES_VERSION = "26.0.0"


def url() -> str:
    return topology.service_url(9696, "/")


def determine_packages(no_tempest: bool = False) -> list[str]:
    """Determine the packages to install for this module."""

    packages = copy.deepcopy(_BASE_PACKAGES)
    if (
        core_apt.PkgVersionCompare("python3-neutron", candidate=True, upstream=True)
        >= NEUTRON_SPLIT_SERVICES_VERSION
    ):
        packages += ["neutron-rpc-server", "neutron-api", "neutron-periodic-workers"]
    else:
        packages += ["neutron-server"]

    return packages


def setup():
    # mask neutron-server if running flamingo.
    if (
        core_apt.PkgVersionCompare("python3-neutron", upstream=True)
        >= NEUTRON_SPLIT_SERVICES_VERSION
    ):
        core_utils.mask_server("neutron-server")

    db_user, db_pass = mysql.ensure_service("neutron")
    rabbit_user, rabbit_pass = rabbitmq.ensure_service("neutron")
    username, password = keystone.ensure_service_account("neutron", "network", url())
    module_utils.cfg_set(
        CONF,
        (
            "database",
            "connection",
            mysql.connection_string("neutron", db_user, db_pass),
        ),
        ("database", "max_pool_size", "1"),
        *module_utils.dict_to_cfg_set_args(
            "DEFAULT",
            {
                "core_plugin": "ml2",
                "dns_domain": "regress-stack.test.",
                "service_plugins": "metering,segments,ovn-router,port_forwarding,trunk",
                "router_distributed": "false",
                "l3_ha": "false",
                "allow_automatic_l3agent_failover": "false",
                "allow_automatic_dhcp_failover": "true",
                "network_scheduler_driver": "neutron.scheduler.dhcp_agent_scheduler.AZAwareWeightScheduler",
                "dhcp_load_type": "networks",
                "router_scheduler_driver": "neutron.scheduler.l3_agent_scheduler.AZLeastRoutersScheduler",
                "dhcp_agents_per_network": "1",
            },
        ),
        ("DEFAULT", "api_workers", "1"),
        ("DEFAULT", "rpc_workers", "1"),
        ("DEFAULT", "transport_url", rabbitmq.transport_url(rabbit_user, rabbit_pass)),
        ("DEFAULT", "notify_nova_on_port_status_changes", "true"),
        ("DEFAULT", "notify_nova_on_port_data_changes", "true"),
        ("oslo_concurrency", "lock_path", "/var/lib/neutron/tmp"),
        *module_utils.dict_to_cfg_set_args(
            "nova", keystone.account_dict(username, password)
        ),
        *module_utils.dict_to_cfg_set_args(
            "placement", keystone.account_dict(username, password)
        ),
        ("DEFAULT", "auth_strategy", "keystone"),
        *module_utils.dict_to_cfg_set_args(
            "keystone_authtoken", keystone.authtoken_service(username, password)
        ),
    )
    module_utils.cfg_set(
        ML2_CONF,
        *module_utils.dict_to_cfg_set_args(
            "ml2",
            {
                "extension_drivers": "port_security,qos,dns_domain_ports,port_forwarding,uplink_status_propagation",
                "type_drivers": "geneve,vlan,flat",
                "tenant_network_types": "geneve,vlan,flat",
                "mechanism_drivers": "ovn",
            },
        ),
        ("ml2_type_vlan", "network_vlan_ranges", "physnet1:1:4094"),
        ("ml2_type_flat", "flat_networks", "physnet1"),
        ("ml2_type_geneve", "vni_ranges", "1:65535"),
        ("ml2_type_geneve", "max_header_size", "38"),
        ("securitygroup", "enable_security_group", "true"),
        ("ovs", "enable_tunneling", "true"),
        ("ovs", "igmp_snooping_enable", "false"),
        *module_utils.dict_to_cfg_set_args(
            "ovn",
            {
                "ovn_nb_connection": ovn.nb_connection(),
                "ovn_sb_connection": ovn.sb_connection(),
                "ovn_l3_scheduler": "leastloaded",
                "ovn_metadata_enabled": "true",
                "enable_distributed_floating_ip": str(
                    topology.distributed_floating_ips()
                ).lower(),
                "dhcp_default_lease_time": "600",
            },
        ),
    )
    module_utils.cfg_set(
        METADATA_AGENT_CONF,
        ("DEFAULT", "nova_metadata_host", core_utils.fqdn()),
        ("DEFAULT", "metadata_proxy_shared_secret", metadata_secret()),
        ("ovs", "ovsdb_connection", topology.ovsdb_connection()),
        ("ovn", "ovn_sb_connection", ovn.sb_connection()),
    )
    topology.shared_sudo(
        "neutron-db-manage",
        ["--config-file", CONF, "--config-file", ML2_CONF, "upgrade", "head"],
        user="neutron",
    )

    # OpenStack 2025.2 (Flamingo) introduced the neutron-rpc-server daemon and
    # deprecated neutron-server.
    if (
        core_apt.PkgVersionCompare("python3-neutron", upstream=True)
        >= NEUTRON_SPLIT_SERVICES_VERSION
    ):
        neutron_daemons = [
            "apache2",  # neutron-api runs under apache2 as a WSGI application
            "neutron-rpc-server",
            "neutron-periodic-workers",
        ]
    else:
        neutron_daemons = [
            "neutron-server",
        ]

    for _daemon in neutron_daemons:
        core_utils.restart_service(_daemon)
    core_utils.restart_service("neutron-ovn-metadata-agent")

    if not topology.owns_shared_state():
        return

    # wait for neutron-server to accept http connections
    for _ in range(10):
        try:
            ensure_public_network()
            break
        except Exception as e:
            if "Connection refused" in str(e):
                LOG.info("Waiting for neutron-server to start...")
                time.sleep(5)
                continue
            raise e


def ensure_public_network():
    """"""
    conn = keystone.o7k()

    # create external network
    network = conn.network.find_network(EXTERNAL_NETWORK, ignore_missing=True)
    if not network:
        network = conn.network.create_network(
            name=EXTERNAL_NETWORK,
            is_router_external=True,
            is_shared=True,
            is_default=True,
            provider_network_type="flat",
            provider_physical_network="physnet1",
        )

    subnet = conn.network.find_subnet("external-subnet", ignore_missing=True)
    if not subnet:
        if provider := topology.provider_network():
            cidr = provider.cidr
            gw, first_host, last_host = (
                provider.gateway,
                provider.allocation_start,
                provider.allocation_end,
            )
        else:
            cidr = ovn.EXTERNAL_CIDR
            hosts = list(ipaddress.ip_network(cidr).hosts())
            gw, first_host, last_host = hosts[0], hosts[1], hosts[-2]
        conn.network.create_subnet(
            name="external-subnet",
            network_id=network.id,
            ip_version=4,
            cidr=cidr,
            gateway_ip=str(gw),
            allocation_pools=[{"start": str(first_host), "end": str(last_host)}],
            enable_dhcp=False,
        )


@functools.lru_cache()
def public_network():
    conn = keystone.o7k()
    return conn.network.find_network(EXTERNAL_NETWORK)


def ensure_network(name: str, project: str):
    conn = keystone.o7k()
    LOG.debug("Ensuring network %r exists...", name)
    network = conn.network.find_network(name, project_id=project, ignore_missing=True)
    if network:
        return network
    LOG.debug("Creating network %r...", name)
    return conn.network.create_network(name=name, project_id=project)


def ensure_subnet(name: str, network, cidr: str):
    conn = keystone.o7k()
    LOG.debug("Ensuring subnet %r exists...", name)
    subnet = conn.network.find_subnet(name, network_id=network.id, ignore_missing=True)
    if subnet:
        return subnet
    LOG.debug("Creating subnet %r...", name)
    return conn.network.create_subnet(
        name=name,
        network_id=network.id,
        ip_version=4,
        cidr=cidr,
    )


def ensure_router(name: str, project):
    conn = keystone.o7k()
    LOG.debug("Ensuring router %r exists...", name)
    router = conn.network.find_router(name, project_id=project.id, ignore_missing=True)
    if router:
        return router
    LOG.debug("Creating router %r...", name)
    return conn.network.create_router(
        name=name,
        project_id=project.id,
        external_gateway_info={"network_id": public_network().id},
    )


def ensure_subnet_router(subnet, router):
    conn = keystone.o7k()
    LOG.debug("Ensuring subnet %r is attached to router %r...", subnet.name, router)

    port_name = subnet.name + "-port"
    port = conn.network.find_port(port_name, ignore_missing=True)
    if not port:
        port = conn.network.create_port(
            name=port_name,
            network_id=subnet.network_id,
            fixed_ips=[{"subnet_id": subnet.id}],
        )
        conn.network.add_interface_to_router(router, port_id=port.id)

    port = conn.network.find_port(port_name)

    if port.device_id != router.id:
        LOG.debug("Reattaching port %r to router %r...", port.name, router)
        conn.network.remove_interface_from_router(port.device_id, port_id=port.id)
        conn.network.add_interface_to_router(router, port_id=port.id)


def metadata_secret() -> str:
    return topology.secret("neutron/metadata", METADATA_SECRET)


def setup_metadata_agent() -> None:
    """Configure only the metadata agent for an explicit deployment."""
    module_utils.cfg_set(
        METADATA_AGENT_CONF,
        ("DEFAULT", "nova_metadata_host", topology.metadata_host()),
        ("DEFAULT", "nova_metadata_port", str(topology.metadata_port())),
        (
            "DEFAULT",
            "metadata_proxy_shared_secret",
            topology.deployment_secret("neutron/metadata"),
        ),
        ("ovs", "ovsdb_connection", topology.ovsdb_connection()),
        ("ovn", "ovn_sb_connection", ovn.sb_connection()),
    )
    core_quiet.run("systemctl", ["enable", "neutron-ovn-metadata-agent"])
    core_quiet.run("systemctl", ["restart", "neutron-ovn-metadata-agent"])


EXPLICIT_ROLES: dict[str, Role] = {
    "controller": Role(("keystone", "mysql", "rabbitmq", "ovn")),
    "compute": Role(
        ("ovn",),
        shared_dependencies=("neutron", "nova"),
        packages=("neutron-ovn-metadata-agent",),
        setup=setup_metadata_agent,
    ),
}
