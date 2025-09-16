# Copyright 2025 - Canonical Ltd
# SPDX-License-Identifier: GPL-3.0-only

import click
import logging
import pathlib

from regress_stack.core import utils
from regress_stack.modules import keystone
from regress_stack.modules import utils as module_utils

LOG = logging.getLogger(__name__)

# Constants for demo resources
DEMO_PASSWORD = "demo123"
FLAVOR_NAME = "m1.small"
NETWORK_NAME = "private-network"
SUBNET_NAME = "private-subnet"
ROUTER_NAME = "private-router"
DEMO_USER = "demo"
DEMO_PROJECT = "demo"


@click.command()
@utils.measure_time
def playground():
    """Setup a basic OpenStack playground environment with Ubuntu image, network, and user."""

    # Get OpenStack connection
    conn = keystone.o7k()

    release = utils.release()
    arch = utils.machine()

    # Image URL based on release and architecture
    image_url = f"http://cloud-images.ubuntu.com/{release}/current/{release}-server-cloudimg-{arch}.img"
    image_name = f"ubuntu-{release}"
    image_file = f"{release}-server-cloudimg-{arch}.img"

    with utils.banner("Setting up playground environment"):
        LOG.info("Release: %s, Architecture: %s", release, arch)

        # Download Ubuntu image
        with utils.measure("Download Ubuntu image"):
            if pathlib.Path(image_file).exists():
                LOG.info("Image file %s already exists, skipping download", image_file)
            else:
                LOG.info("Downloading image from %s", image_url)
                utils.run("wget", [image_url, "-O", image_file])

        # Create image in OpenStack
        with utils.measure("Create OpenStack image"):
            LOG.info("Creating image %s in OpenStack", image_name)
            image = conn.image.find_image(image_name, ignore_missing=True)
            if not image:
                image = conn.image.create_image(
                    name=image_name,
                    filename=image_file,
                    disk_format="qcow2",
                    container_format="bare",
                    visibility="public",
                    wait=True,
                )
                LOG.info("Created image %s", image_name)
            else:
                LOG.info("Image %s already exists", image_name)

        # Create flavor
        with utils.measure("Create flavor"):
            LOG.info("Creating flavor %s", FLAVOR_NAME)
            flavor = conn.compute.find_flavor(FLAVOR_NAME, ignore_missing=True)
            if not flavor:
                flavor = conn.compute.create_flavor(
                    name=FLAVOR_NAME, vcpus=1, ram=1024, disk=5, is_public=True
                )
                LOG.info("Created flavor %s", FLAVOR_NAME)
            else:
                LOG.info("Flavor %s already exists", FLAVOR_NAME)

        # Create demo project
        with utils.measure("Create demo project"):
            LOG.info("Creating demo project")
            demo_project = conn.identity.find_project(DEMO_PROJECT, ignore_missing=True)
            if not demo_project:
                demo_project = conn.identity.create_project(
                    name=DEMO_PROJECT, domain_id=keystone.default_domain()
                )
                LOG.info("Created project %s", DEMO_PROJECT)
            else:
                LOG.info("Project %s already exists", DEMO_PROJECT)

        # Create demo user
        with utils.measure("Create demo user"):
            LOG.info("Creating demo user")
            demo_user = conn.identity.find_user(DEMO_USER, ignore_missing=True)
            if not demo_user:
                demo_user = conn.identity.create_user(
                    name=DEMO_USER,
                    password=DEMO_PASSWORD,
                    email="demo@example.com",
                    domain_id=keystone.default_domain(),
                )
                LOG.info("Created user %s", DEMO_USER)
            else:
                LOG.info("User %s already exists", DEMO_USER)

        # Add user to project with member role
        with utils.measure("Add user to project"):
            LOG.info("Adding demo user to demo project")
            member_role = conn.identity.find_role("member", ignore_missing=True)
            if not member_role:
                member_role = conn.identity.find_role("_member_", ignore_missing=True)
            if member_role:
                conn.identity.assign_project_role_to_user(
                    demo_project, demo_user, member_role
                )
                LOG.info(
                    "Assigned member role to user %s in project %s",
                    DEMO_USER,
                    DEMO_PROJECT,
                )

        # Create private network
        with utils.measure("Create private network"):
            LOG.info("Creating private network")
            network = conn.network.find_network(NETWORK_NAME, ignore_missing=True)
            if not network:
                network = conn.network.create_network(
                    name=NETWORK_NAME, project_id=demo_project.id
                )
                LOG.info("Created network %s", NETWORK_NAME)
            else:
                LOG.info("Network %s already exists", NETWORK_NAME)

        # Create subnet
        with utils.measure("Create subnet"):
            LOG.info("Creating private subnet")
            subnet = conn.network.find_subnet(SUBNET_NAME, ignore_missing=True)
            if not subnet:
                subnet = conn.network.create_subnet(
                    name=SUBNET_NAME,
                    network_id=network.id,
                    ip_version=4,
                    cidr="192.168.133.0/24",
                    project_id=demo_project.id,
                )
                LOG.info("Created subnet %s", SUBNET_NAME)
            else:
                LOG.info("Subnet %s already exists", SUBNET_NAME)

        # Create router
        with utils.measure("Create router"):
            LOG.info("Creating private router")
            router = conn.network.find_router(ROUTER_NAME, ignore_missing=True)
            if not router:
                # Check if external network exists
                external_network = conn.network.find_network(
                    "external-network", ignore_missing=True
                )
                external_gateway_info = None
                if external_network:
                    external_gateway_info = {"network_id": external_network.id}
                else:
                    LOG.warning(
                        "External network 'external-network' not found, creating router without external gateway"
                    )

                router = conn.network.create_router(
                    name=ROUTER_NAME,
                    project_id=demo_project.id,
                    external_gateway_info=external_gateway_info,
                )
                LOG.info("Created router %s", ROUTER_NAME)
            else:
                LOG.info("Router %s already exists", ROUTER_NAME)

        # Add subnet to router
        with utils.measure("Add subnet to router"):
            LOG.info("Adding subnet to router")
            # Check if subnet is already connected to router
            port_name = f"{SUBNET_NAME}-port"
            port = conn.network.find_port(port_name, ignore_missing=True)
            if not port:
                try:
                    conn.network.add_interface_to_router(router, subnet_id=subnet.id)
                    LOG.info("Added subnet %s to router %s", SUBNET_NAME, ROUTER_NAME)
                except Exception as e:
                    LOG.warning("Failed to add subnet to router: %s", e)
            else:
                LOG.info(
                    "Subnet %s already connected to router %s", SUBNET_NAME, ROUTER_NAME
                )

        # Update default security group for demo project
        with utils.measure("Update default security group"):
            LOG.info("Updating default security group for demo project")
            # Find the default security group for the demo project
            default_security_group = None
            for sg in conn.network.security_groups(project_id=demo_project.id):
                if sg.name == "default":
                    default_security_group = sg
                    break

            if default_security_group:
                LOG.info("Found default security group %s", default_security_group.id)

                # Check if SSH rule already exists
                ssh_rule_exists = False
                icmp_rule_exists = False

                for rule in conn.network.security_group_rules(
                    security_group_id=default_security_group.id
                ):
                    if (
                        rule.direction == "ingress"
                        and rule.protocol == "tcp"
                        and rule.port_range_min == 22
                        and rule.port_range_max == 22
                    ):
                        ssh_rule_exists = True
                    elif rule.direction == "ingress" and rule.protocol == "icmp":
                        icmp_rule_exists = True

                # Add SSH rule if it doesn't exist
                if not ssh_rule_exists:
                    conn.network.create_security_group_rule(
                        security_group_id=default_security_group.id,
                        direction="ingress",
                        protocol="tcp",
                        port_range_min=22,
                        port_range_max=22,
                        remote_ip_prefix="0.0.0.0/0",
                    )
                    LOG.info("Added SSH rule to default security group")
                else:
                    LOG.info("SSH rule already exists in default security group")

                # Add ICMP rule if it doesn't exist
                if not icmp_rule_exists:
                    conn.network.create_security_group_rule(
                        security_group_id=default_security_group.id,
                        direction="ingress",
                        protocol="icmp",
                        remote_ip_prefix="0.0.0.0/0",
                    )
                    LOG.info("Added ICMP rule to default security group")
                else:
                    LOG.info("ICMP rule already exists in default security group")
            else:
                LOG.warning("Default security group not found for demo project")

        # Generate user credentials file
        with utils.measure("Generate user credentials"):
            LOG.info("Generating user credentials file")
            auth_url = keystone.OS_AUTH_URL

            user_rc_content = f"""# Demo user credentials for OpenStack
export OS_USERNAME={DEMO_USER}
export OS_PASSWORD={DEMO_PASSWORD}
export OS_PROJECT_NAME={DEMO_PROJECT}
export OS_USER_DOMAIN_NAME=Default
export OS_PROJECT_DOMAIN_NAME=Default
export OS_AUTH_URL={auth_url}
export OS_IDENTITY_API_VERSION=3
export OS_REGION_NAME={module_utils.REGION}

# Usage: source ~/user.rc
"""

            pathlib.Path("~/user.rc").expanduser().write_text(user_rc_content)
            LOG.info("User credentials written to ~/user.rc")

    with utils.banner("Playground setup complete"):
        LOG.info("To use the demo user environment, run: source ~/user.rc")
