# Copyright 2025 - Canonical Ltd
# SPDX-License-Identifier: GPL-3.0-only

import click
import logging

import regress_stack.modules
from regress_stack.core import utils
from regress_stack.core.modules import get_execution_order
from regress_stack.cli.utils import collect_logs

LOG = logging.getLogger(__name__)


@click.command()
@click.argument("target", required=False)
@utils.measure_time
def setup(target):
    """Execute the setup phase for modules."""
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
