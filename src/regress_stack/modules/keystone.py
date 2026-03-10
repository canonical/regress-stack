# Copyright 2025 - Canonical Ltd
# SPDX-License-Identifier: GPL-3.0-only

import functools
import logging
import os
import pathlib
import typing

import openstack

from regress_stack.core import utils as core_utils
from regress_stack.modules import mysql, utils
from regress_stack.modules import utils as module_utils

LOG = logging.getLogger(__name__)

DEPENDENCIES = {
    mysql,
}
PACKAGES = ["keystone", "apache2", "libapache2-mod-wsgi-py3"]
LOGS = ["/var/log/keystone/"]

CONF = "/etc/keystone/keystone.conf"
ADMIN_PASSWORD = "changeme"
OS_AUTH_URL = f"http://{core_utils.my_ip()}:5000/v3/"
SERVICE_DOMAIN = "service"
SERVICE_PROJECT = "service"
RESOURCE_PKG = "regress_stack.resources"
PUBLIC_WSGI = pathlib.Path("/usr/bin/keystone-wsgi-public")
ADMIN_WSGI = pathlib.Path("/usr/bin/keystone-wsgi-admin")


def _ensure_wsgi_scripts() -> None:
    for resource, destination in (
        ("keystone-wsgi-public", PUBLIC_WSGI),
        ("keystone-wsgi-admin", ADMIN_WSGI),
    ):
        if destination.exists():
            continue
        core_utils.warn_workaround(
            "keystone packaging",
            f"{destination.name} is missing; installing a local shim until the Ubuntu package is fixed",
        )
        core_utils.write_resource(
            RESOURCE_PKG,
            resource,
            destination,
            mode=0o755,
        )


def setup():
    username, password = mysql.ensure_service("keystone")
    _ensure_wsgi_scripts()
    core_utils.run(
        "sed",
        [
            "-i",
            "s|keystone-public processes=5 threads=1|keystone-public processes=1 threads=1|",
            "/etc/apache2/sites-enabled/keystone.conf",
        ],
    )
    module_utils.cfg_set(
        CONF,
        (
            "database",
            "connection",
            mysql.connection_string("keystone", username, password),
        ),
        ("database", "max_pool_size", "1"),
        ("token", "provider", "fernet"),
    )
    LOG.debug("Running keystone-manage db_sync...")
    core_utils.sudo(
        "keystone-manage",
        ["--config-dir", "/etc/keystone", "db_sync"],
        user="keystone",
    )
    opts = "--keystone-user", "keystone", "--keystone-group", "keystone"
    LOG.debug("Running bootstrapping keystone...")
    core_utils.run("keystone-manage", ["fernet_setup", *opts])
    core_utils.run("keystone-manage", ["credential_setup", *opts])
    core_utils.run(
        "keystone-manage",
        [
            "bootstrap",
            "--bootstrap-password",
            ADMIN_PASSWORD,
            "--bootstrap-admin-url",
            OS_AUTH_URL,
            "--bootstrap-internal-url",
            OS_AUTH_URL,
            "--bootstrap-public-url",
            OS_AUTH_URL,
            "--bootstrap-region-id",
            utils.REGION,
        ],
    )
    core_utils.restart_apache()
    authrc = auth_rc()
    print(authrc)
    pathlib.Path("~/auth.rc").expanduser().write_text(authrc)
    domain = ensure_domain(SERVICE_DOMAIN)
    ensure_project(SERVICE_PROJECT, domain.id)
    ensure_role("_member_")


def auth_env() -> typing.Dict[str, str]:
    return {
        "OS_USERNAME": "admin",
        "OS_PASSWORD": ADMIN_PASSWORD,
        "OS_PROJECT_NAME": "admin",
        "OS_USER_DOMAIN_NAME": "Default",
        "OS_PROJECT_DOMAIN_NAME": "Default",
        "OS_AUTH_URL": OS_AUTH_URL,
        "OS_IDENTITY_API_VERSION": "3",
        "OS_REGION_NAME": utils.REGION,
    }


def account_dict(service: str, password: str) -> typing.Dict[str, str]:
    return {
        "auth_url": OS_AUTH_URL,
        "auth_type": "password",
        "project_domain_name": SERVICE_DOMAIN,
        "user_domain_name": SERVICE_DOMAIN,
        "project_name": SERVICE_PROJECT,
        "username": service,
        "password": password,
        "region_name": utils.REGION,
    }


def authtoken_service(service: str, password: str) -> typing.Dict[str, str]:
    return {
        **account_dict(service, password),
        "www_authenticate_uri": OS_AUTH_URL,
        "service_token_roles": "admin",
        "service_token_roles_required": "true",
    }


def auth_rc():
    return "\n".join(f"export {k}={v}" for k, v in auth_env().items())


@functools.lru_cache()
def o7k():
    os.environ.update(auth_env())
    conn = openstack.connect(load_envvars=True)
    return conn


@functools.lru_cache()
def region() -> str:
    conn = o7k()
    return conn.identity.find_region(utils.REGION).id


def ensure_domain(name: str):
    conn = o7k()
    LOG.debug("Ensuring domain %r exists...", name)
    domain = conn.identity.find_domain(name, ignore_missing=True)
    if domain:
        return domain
    LOG.debug("Creating domain %r...", name)
    return conn.identity.create_domain(name=name)


@functools.lru_cache()
def service_domain() -> str:
    conn = o7k()
    return conn.identity.find_domain(SERVICE_DOMAIN).id


@functools.lru_cache()
def default_domain() -> str:
    conn = o7k()
    return conn.identity.find_domain("Default").id


@functools.lru_cache()
def admin_user():
    conn = o7k()
    return conn.identity.find_user("admin", domain_id=default_domain())


def ensure_project(name: str, domain: str):
    conn = o7k()
    LOG.debug("Ensuring project %r exists...", name)

    project = conn.identity.find_project(name, domain_id=domain, ignore_missing=True)
    if project:
        return project
    LOG.debug("Creating project %r...", name)
    return conn.identity.create_project(name=name, domain_id=domain)


@functools.lru_cache()
def service_project() -> str:
    conn = o7k()
    return conn.identity.find_project(SERVICE_PROJECT, service_domain()).id


def ensure_service_account(name: str, type: str, url: str) -> typing.Tuple[str, str]:
    """Ensure service account exists for a given service.


    Args:
        name: Name of the service.

    Returns:
        Tuple of (username, password).
    """
    password = "changeme"

    user = ensure_user(name, password, service_domain())
    ensure_admin(user, service_project())
    service = ensure_service(name, type)
    ensure_endpoint(service, url)
    return name, password


def ensure_user(name, password, domain):
    conn = o7k()
    LOG.debug("Ensuring user %r exists...", name)

    user = conn.identity.find_user(name, domain_id=domain, ignore_missing=True)
    if user:
        return user
    LOG.debug("Creating user %r...", name)
    return conn.identity.create_user(name=name, password=password, domain_id=domain)


@functools.lru_cache()
def admin_role():
    conn = o7k()
    return conn.identity.find_role("admin")


def ensure_role(name: str):
    conn = o7k()
    LOG.debug("Ensuring role %r exists...", name)
    role = conn.identity.find_role(name, ignore_missing=True)
    if role:
        return role
    LOG.debug("Creating role %r...", name)
    return conn.identity.create_role(name=name)


def ensure_admin(user, project):
    conn = o7k()
    LOG.debug("Ensuring user %r is admin of project %r...", user.name, project)

    conn.identity.assign_project_role_to_user(project, user, admin_role().id)


def ensure_service(name: str, type: str):
    conn = o7k()
    LOG.debug("Ensuring service %r exists...", name)
    service = conn.identity.find_service(name, ignore_missing=True)
    if service:
        return service
    LOG.debug("Creating service %r...", name)
    return conn.identity.create_service(name=name, type=type)


def _ensure_endpoint_interface(
    conn, service, url: str, region: str, interface: str, endpoints: list
):
    for endpoint in endpoints:
        if endpoint.interface == interface:
            return endpoint

    LOG.debug("Creating endpoint %r:%s...", service.name, interface)
    return conn.identity.create_endpoint(
        service_id=service.id, url=url, interface=interface, region_id=region
    )


def ensure_endpoint(service, url: str):
    conn = o7k()
    LOG.debug("Ensuring endpoints %r exists...", service.name)
    endpoints = list(conn.identity.endpoints(service_id=service.id))
    for interface in ("public", "internal", "admin"):
        _ensure_endpoint_interface(conn, service, url, region(), interface, endpoints)
    # Clear connection after updating endpoints
    conn.close()
    o7k.cache_clear()


def grant_domain_role(user, role, domain):
    conn = o7k()
    LOG.debug("Granting role %r to user %r...", role, user)
    try:
        domain.assign_role_to_user(conn.identity, user, role, False)
    except TypeError as e:
        # Fallback for caracal and under
        if "were given" in str(e):
            domain.assign_role_to_user(conn.identity, user, role)
        else:
            raise e


def grant_project_role(user, role, project):
    conn = o7k()
    LOG.debug("Granting role %r to user %r...", role, user)
    try:
        project.assign_role_to_user(conn.identity, user, role, False)
    except TypeError as e:
        # Fallback for caracal and under
        if "were given" in str(e):
            project.assign_role_to_user(conn.identity, user, role)
        else:
            raise e
