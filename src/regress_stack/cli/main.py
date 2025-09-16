# Copyright 2025 - Canonical Ltd
# SPDX-License-Identifier: GPL-3.0-only

import click
import logging

from regress_stack.cli import plan as plan_module
from regress_stack.cli import setup as setup_module
from regress_stack.cli import test as test_module
from regress_stack.cli import list_modules as list_modules_module
from regress_stack.cli import packages as packages_module
from regress_stack.cli import playground as playground_module


@click.group(
    name="openstack-deb-tester",
    help="A CLI tool for testing OpenStack Debian packages.",
)
def main():
    """OpenStack Debian package testing tool."""
    logging.basicConfig(level=logging.DEBUG)


# Register all commands
main.add_command(plan_module.plan)
main.add_command(setup_module.setup)
main.add_command(test_module.test)
main.add_command(list_modules_module.list_modules)
main.add_command(packages_module.packages)
main.add_command(playground_module.playground)


if __name__ == "__main__":
    main()
