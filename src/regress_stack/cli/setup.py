# Copyright 2025 - Canonical Ltd
# SPDX-License-Identifier: GPL-3.0-only

import click
import logging
from pathlib import Path

import regress_stack.modules
from regress_stack.core import utils
from regress_stack.core.modules import get_execution_order
from regress_stack.cli.utils import collect_logs

LOG = logging.getLogger(__name__)


@click.command()
@click.argument("target", required=False)
@click.option("--inventory", type=click.Path(exists=True, path_type=Path))
@click.option("--node", help="Bootstrap node name from the inventory.")
@click.option("--preseed", type=click.Path(exists=True, path_type=Path))
@click.option("--export-preseeds", type=click.Path(path_type=Path))
@utils.measure_time
def setup(target, inventory=None, node=None, preseed=None, export_preseeds=None):
    """Execute the setup phase for modules."""
    if inventory or preseed or node or export_preseeds:
        if target:
            raise click.UsageError(
                "Explicit profiles configure the complete local role; omit TARGET"
            )
        from regress_stack.multinode.setup import run

        try:
            run(inventory, node, preseed, export_preseeds)
        except (ValueError, RuntimeError, OSError) as error:
            raise click.ClickException(str(error)) from None
        click.echo(
            "Local setup complete; run 'regress-stack ready' after all nodes have joined."
        )
        return
    try:
        for mod in get_execution_order(regress_stack.modules, target):
            if setup_func := getattr(mod.module, "setup", None):
                with utils.measure("setup " + mod.name):
                    setup_func()
                    utils.mark_setup(mod.name)
    except Exception as e:
        LOG.error("Failed to setup %s: %s", target, e)
        collect_logs()
        raise
