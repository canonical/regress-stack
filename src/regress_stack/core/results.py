# Copyright 2026 - Canonical Ltd
# SPDX-License-Identifier: GPL-3.0-only

"""Expose test outcomes without publishing arbitrary subprocess attachments."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import os
from pathlib import Path
import re
import subprocess
from urllib.parse import quote, quote_plus

# Only standard library dependencies: the CI host imports result_line().
from regress_stack.core.quiet import CommandError


def result_line(line: str) -> str | None:
    """Allow only subunit-trace test outcomes and numeric summary lines.

    Tracebacks, HTTP bodies, attachments, and skip reasons can contain secrets.
    These are deliberately excluded from the live console stream.
    """
    line = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", line).rstrip("\r\n")
    outcome = re.fullmatch(
        r"(\{\d+\} [A-Za-z_][A-Za-z0-9_.\[\],=:-]*"
        r"(?: \[[0-9.s]+(?: \([0-9.+%-]+\))?\])? \.\.\. "
        r"(?:ok|FAILED|SKIPPED|xfail|uxsuccess))(?:.*)",
        line,
    )
    if outcome:
        return outcome[1].replace("::", ": :")
    if re.fullmatch(
        r"(?:Totals|Worker Balance|=+|Ran: \d+ tests in [0-9.]+ sec\.|"
        r" - (?:Passed|Skipped|Expected Fail|Unexpected Success|Failed): \d+|"
        r"Sum of execute time for each test: [0-9.]+ sec\.|"
        r" - Worker \d+ \(\d+ tests\) => [0-9:.]+s?)",
        line,
    ):
        return line
    return None


def run(
    command: str,
    args: Sequence[str],
    *,
    env: Mapping[str, str] | None = None,
    cwd: str | os.PathLike[str] | None = None,
    secrets: Sequence[str] = (),
) -> int:
    """Stream safe test results and return the actual subprocess exit status.

    Each secret is redacted from the published lines.
    """
    try:
        with subprocess.Popen(
            [command, *args],
            env=env,
            cwd=cwd,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            bufsize=1,
        ) as process:
            if process.stdout is None:
                raise RuntimeError("Test output pipe was not created")
            for line in process.stdout:
                output = result_line(line)
                if output is not None:
                    for value in secrets:
                        if len(value) >= 8:
                            for encoded in (
                                value,
                                quote(value, safe=""),
                                quote_plus(value),
                            ):
                                output = output.replace(encoded, "<redacted>")
                    print(output, flush=True)
            return process.wait()
    except FileNotFoundError:
        raise FileNotFoundError(
            f"Local executable {Path(command).name} was not found"
        ) from None
    except OSError:
        raise CommandError(1, [Path(command).name]) from None
