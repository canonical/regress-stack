# Copyright 2025 - Canonical Ltd
# SPDX-License-Identifier: GPL-3.0-only

import click
from pathlib import Path
import uuid

import regress_stack.modules
from regress_stack.core.modules import get_execution_order


@click.command("packages")
@click.option(
    "--no-tempest",
    is_flag=True,
    help="Do not include tempest related packages, this is useful when using the tempest snap.",
)
@click.argument("target", required=False)
@click.option("--inventory", type=click.Path(exists=True, path_type=Path))
@click.option("--node")
@click.option("--preseed", type=click.Path(exists=True, path_type=Path))
def packages(target=None, no_tempest=False, inventory=None, node=None, preseed=None):
    """List packages needed to reach the specified target.

    If no target is specified, lists packages for all modules.
    The output can be fed directly to 'apt install' command.

    Examples:
        regress-stack packages nova
        regress-stack packages --no-tempest nova
        apt install $(regress-stack packages nova)
    """
    if inventory or node or preseed:
        from regress_stack.core.deployment import Context, Deployment
        from regress_stack.core.profiles import packages as local_packages

        if (
            target
            or (inventory is None) == (preseed is None)
            or (inventory and not node)
            or (preseed and node)
        ):
            raise click.UsageError(
                "Use --inventory with --node, or --preseed, without TARGET"
            )
        try:
            context = (
                Context.read(preseed)
                if preseed
                else Context(Deployment.read(inventory), node, str(uuid.uuid4()))
            )
            click.echo(" ".join(local_packages(context, no_tempest=no_tempest)))
        except (ValueError, RuntimeError, OSError) as error:
            raise click.ClickException(str(error)) from None
        return
    try:
        # Get execution order without filtering for missing dependencies
        execution_order = get_execution_order(
            regress_stack.modules, target, filter_missing=False
        )

        # Collect all packages
        all_packages = regress_stack.modules.determine_packages(no_tempest=no_tempest)
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
