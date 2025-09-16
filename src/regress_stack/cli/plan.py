# Copyright 2025 - Canonical Ltd
# SPDX-License-Identifier: GPL-3.0-only

import click
from pprint import pprint

import regress_stack.modules
from regress_stack.core.modules import get_execution_order


@click.command()
@click.argument("target", required=False)
def plan(target):
    """Plan the test execution order for modules."""
    order = get_execution_order(regress_stack.modules, target)
    print("Execution Order:")
    pprint(order)
