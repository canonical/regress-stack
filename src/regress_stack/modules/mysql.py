# Copyright 2025 - Canonical Ltd
# SPDX-License-Identifier: GPL-3.0-only

import logging
import typing

from regress_stack import topology
from regress_stack.core import utils as core_utils
from regress_stack.roles import Role

LOG = logging.getLogger(__name__)

LOGS = ["/var/log/mysql/"]
PACKAGES = ["mysql-server"]


def setup():
    pass


CREATE_USER = """CREATE USER '{name}'@'localhost' IDENTIFIED BY '{password}';
CREATE USER '{name}'@'%'         IDENTIFIED BY '{password}';
"""

GRANT_USER = """ GRANT ALL PRIVILEGES ON {name}.* TO '{database}'@'localhost';
GRANT ALL PRIVILEGES ON {name}.* TO '{database}'@'%';
"""


def get_host():
    return topology.database_host()


def connection_string(database: str, username: str, password: str):
    return f"mysql+pymysql://{username}:{password}@{get_host()}/{database}"


def ensure_service(name: str) -> typing.Tuple[str, str]:
    """Ensure service account exists for a given service.

    Database name is the same as the service name.

    Args:
        name: Name of the service.

    Returns:
        Tuple of (username, password).
    """
    if topology.explicit():
        return topology.database_account(name)
    password = "changeme"

    ensure_database(name)
    ensure_user(name, password)
    grant_user(name, name)
    return name, password


def ensure_database(name: str):
    """Ensure that a database exists."""
    # check if exists
    LOG.debug("Checking if database %r exists...", name)
    tpl = """SELECT SCHEMA_NAME FROM INFORMATION_SCHEMA.SCHEMATA WHERE SCHEMA_NAME = '{database}';"""
    databases = core_utils.run("mysql", ["-u", "root", "-e", tpl.format(database=name)])
    if databases:
        LOG.debug("Database %r already exists.", name)
        return
    LOG.debug("Database %r does not exist. Creating...", name)
    core_utils.run("mysql", ["-u", "root", "-e", "CREATE DATABASE {};".format(name)])


def ensure_user(name, password):
    """Ensure that a user exists."""
    LOG.debug("Checking if user %r exists...", name)
    tpl = """SELECT User FROM mysql.user WHERE User = '{name}';"""
    users = core_utils.run("mysql", ["-u", "root", "-e", tpl.format(name=name)])
    if users:
        LOG.debug("User %r already exists.", name)
        return
    LOG.debug("User %r does not exist. Creating...", name)
    core_utils.run(
        "mysql", ["-u", "root", "-e", CREATE_USER.format(name=name, password=password)]
    )


def grant_user(name, database):
    """Grant user access to a database."""
    LOG.debug("Granting user %r access to database %r...", name, database)
    core_utils.run(
        "mysql", ["-u", "root", "-e", GRANT_USER.format(name=name, database=database)]
    )


EXPLICIT_ROLES: dict[str, Role] = {
    "controller": Role(("utils",), configured_by="recipe"),
}
