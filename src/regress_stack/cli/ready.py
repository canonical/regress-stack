# Copyright 2026 - Canonical Ltd
# SPDX-License-Identifier: GPL-3.0-only

from pathlib import Path

import click

from regress_stack.multinode import readiness
from regress_stack.multinode.setup import LOCAL_STATE, quiet_context, read_state
from regress_stack.cli.utils import EXPLICIT_ERRORS


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
        context = read_state(state)
        with quiet_context(context, bootstrap_only=False):
            failures = readiness.check(context, unavailable_node)
    except EXPLICIT_ERRORS as error:
        raise click.ClickException(str(error)) from None
    if failures:
        raise click.ClickException("Not ready: " + ", ".join(failures))
    click.echo(
        "Deployment ready"
        + (" (degraded)" if unavailable_node else "")
        + "; workload/failure acceptance is a separate harness test."
    )
