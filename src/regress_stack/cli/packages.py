# Copyright 2025 - Canonical Ltd
# SPDX-License-Identifier: GPL-3.0-only

import click

import regress_stack.modules
from regress_stack.core.modules import get_execution_order


@click.command("packages")
@click.help_option(
    "--no-tempest",
    is_flag=True,
    help="Do not include tempest related packages, this is useful when using the tempest snap.",
)
@click.argument("target", required=False)
def packages(target=None, no_tempest=False):
    """List packages needed to reach the specified target.

    If no target is specified, lists packages for all modules.
    The output can be fed directly to 'apt install' command.

    Examples:
        regress-stack packages nova
        regress-stack packages --no-tempest nova
        apt install $(regress-stack packages nova)
    """
    try:
        # Get execution order without filtering for missing dependencies
        execution_order = get_execution_order(
            regress_stack.modules, target, filter_missing=False
        )

        # Collect all packages
        all_packages = []
        for module_comp in execution_order:
            if hasattr(module_comp.module, "determine_packages"):
                packages_list = module_comp.module.determine_packages(
                    no_tempest=no_tempest
                )
            else:
                packages_list = getattr(module_comp.module, "PACKAGES", [])

            all_packages.extend(packages_list)

        # Remove duplicates while preserving order
        seen = set()
        unique_packages = []
        for pkg in all_packages:
            if pkg not in seen:
                seen.add(pkg)
                unique_packages.append(pkg)

        # Output packages space-separated for apt install
        print(" ".join(unique_packages))

    except RuntimeError as e:
        click.echo(f"Error: {e}", err=True)
        raise click.Abort()
