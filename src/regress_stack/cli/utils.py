# Copyright 2025 - Canonical Ltd
# SPDX-License-Identifier: GPL-3.0-only

import pathlib

import regress_stack.modules
from regress_stack.core import utils
from regress_stack.core.modules import get_execution_order


def _output_log_file(path: pathlib.Path):
    """Output the contents of a log file to stdout."""
    with path.open(encoding="utf-8", errors="replace") as log_file:
        for line in log_file:
            print(line, end="")


def collect_logs():
    """Collect and output logs from all modules and the system journal."""
    for mod in get_execution_order(regress_stack.modules, None):
        logs = getattr(mod.module, "LOGS", None)
        if not logs:
            continue
        with utils.banner(f"Collecting logs for {mod.module.__name__}"):
            for log in logs:
                log_path = pathlib.Path(log)
                if not log_path.exists():
                    continue
                if log_path.is_dir():
                    for log_file in log_path.iterdir():
                        _output_log_file(log_file)
                else:
                    _output_log_file(log_path)
    utils.print_ascii_banner("Collecting journal logs")
    utils.run("journalctl", ["-o", "short-precise", "--no-pager"])
    utils.print_ascii_banner("Collected journal logs")
