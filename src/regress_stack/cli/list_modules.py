# Copyright 2025 - Canonical Ltd
# SPDX-License-Identifier: GPL-3.0-only

import click

import regress_stack.modules
from regress_stack.core.modules import get_execution_order, modules


@click.command("list-modules")
def list_modules():
    """List all available modules in the system."""
    _ = get_execution_order(regress_stack.modules)
    for module in modules():
        print(module)
