# Copyright 2026 - Canonical Ltd
# SPDX-License-Identifier: GPL-3.0-only

from pathlib import Path

import click

from regress_stack.core.deployment import Context
from regress_stack.multinode import readiness
from regress_stack.multinode.setup import LOCAL_STATE, quiet_context


@click.command()
@click.option(
    "--state", type=click.Path(exists=True, path_type=Path), default=str(LOCAL_STATE)
)
@click.option(
    "--unavailable-node",
    help="Check degraded readiness while this known node is stopped by the harness.",
)
def ready(state, unavailable_node):
    """Check live deployment membership and API readiness on a controller."""
    try:
        context = Context.read(state)
        with quiet_context(context, bootstrap_only=False):
            failures = readiness.check(context, unavailable_node)
    except (ValueError, RuntimeError, OSError) as error:
        raise click.ClickException(str(error)) from None
    if failures:
        raise click.ClickException("Not ready: " + ", ".join(failures))
    click.echo(
        "Deployment ready"
        + (" (degraded)" if unavailable_node else "")
        + "; workload/failure acceptance is a separate harness test."
    )
