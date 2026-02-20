# Copyright 2025 - Canonical Ltd
# SPDX-License-Identifier: GPL-3.0-only

import click
import logging
import os
import json
import pathlib
import subprocess

import regress_stack.modules
from regress_stack.core import apt as core_apt
from regress_stack.core import utils
from regress_stack.core.modules import get_execution_order
from regress_stack.modules import keystone
from regress_stack.modules import utils as module_utils
from regress_stack.cli.utils import collect_logs

LOG = logging.getLogger(__name__)


@click.command()
@click.option(
    "--concurrency",
    type=str,
    default="1",
    callback=lambda ctx, param, value: utils.concurrency_cb(value)
    if value != "1"
    else 1,
    help="The number of workers to use, defaults to 1. The value 'auto' sets concurrency to number of cpus / 3.",
)
@click.option(
    "--retry-failed",
    type=int,
    default=0,
    help="Number of times to retry failed tests, defaults to 0 (no retries).",
)
@utils.measure_time
def test(concurrency, retry_failed):
    """Run the regression tests using Tempest."""

    # NOTE(freyes): use PPA to fix http://pad.lv/2141604 if needed.
    if core_apt.PkgVersionCompare("python3-tempestconf") < "3.5.1-1ubuntu1~cloud0":
        core_apt.add_ppa("ppa:freyes/lp2141604")
        utils.run("apt", ["install", "-yq", "--only-upgrade", "python3-tempestconf"])
    env = os.environ.copy()
    env.update(keystone.auth_env())
    dir_name = "mycloud01"
    release = utils.release()
    workspaces = json.loads(
        utils.run("tempest", ["workspace", "list", "--format", "json"])
    )
    workspaces = [ws["Name"] for ws in workspaces]
    if dir_name in workspaces:
        LOG.info("Tempest workspace %s already exists, skipping init", dir_name)
    else:
        utils.run("tempest", ["init", dir_name])

    utils.run(
        "discover-tempest-config",
        [
            "--create",
            "--flavor-min-mem",
            "1024",
            "--flavor-min-disk",
            "5",
            "--image",
            f"http://cloud-images.ubuntu.com/{release}/current/{release}-server-cloudimg-{utils.machine()}.img",
        ],
        env=env,
        cwd=dir_name,
    )
    tempest_conf = pathlib.Path(dir_name) / "etc" / "tempest.conf"
    module_utils.cfg_set(
        str(tempest_conf),
        ("validation", "image_ssh_user", "ubuntu"),
        ("validation", "image_alt_ssh_user", "ubuntu"),
    )

    test_regexes = []
    for mod in get_execution_order(regress_stack.modules):
        if not utils.is_setup_done(mod.name):
            LOG.info("Skipping %s", mod.name)
            continue
        if configure := getattr(mod.module, "configure_tempest", None):
            with utils.measure("configure_tempest " + mod.name):
                configure(tempest_conf)
        includes_regexes = getattr(mod.module, "TEST_INCLUDE_REGEXES", [])
        exclude_regexes = getattr(mod.module, "TEST_EXCLUDE_REGEXES", [])
        test_regexes.append((includes_regexes, exclude_regexes))

    test_regexes.append(
        (
            os.environ.get("TEST_INCLUDE_REGEXES", "").split("|"),
            os.environ.get("TEST_EXCLUDE_REGEXES", "").split("|"),
        )
    )

    LOG.info("Building test list")
    global_include_regex = ["smoke"]
    global_exclude_regex = []

    for include_regexes, exclude_regexes in test_regexes:
        if include_regexes and include_regexes[0]:
            global_include_regex.append("|".join(include_regexes))
        if exclude_regexes and exclude_regexes[0]:
            global_exclude_regex.append("|".join(exclude_regexes))

    regress_tests = utils.run(
        "tempest",
        [
            "run",
            "--list",
            "--regex",
            "|".join(global_include_regex),
            "--exclude-regex",
            "|".join(global_exclude_regex),
        ],
        env=env,
        cwd=dir_name,
    )

    regress_list = pathlib.Path(dir_name) / "regress_tests.txt"
    regress_list.write_text(regress_tests)

    # The tempest run is a long-running process and to improve UX we want
    # direct output of both STDOUT and STDERR.
    #
    # Implementing that with subprocess is complicated, and as we do not need
    # to process the output we can use system().
    load_list = str(regress_list.relative_to(dir_name))
    utils.system(
        f"tempest run --load-list {load_list} --concurrency {concurrency}",
        env,
        dir_name,
    )

    retries = 0
    successful_run = False
    while retry_failed >= retries and not successful_run:
        try:
            with utils.banner("Fetching failing tests"):
                utils.run("stestr", ["failing", "--list"], cwd=dir_name)
                successful_run = True
        except subprocess.CalledProcessError:
            retries += 1
            # Collect logs after the last retry to avoid collecting logs
            # multiple times in case of multiple retries.
            if retries > retry_failed:
                collect_logs()
                raise
            else:
                LOG.warning(
                    "Failed to fetch failing tests, retrying (%d/%d)",
                    retries,
                    retry_failed,
                )
                utils.system(
                    f"stestr run --failing --concurrency {concurrency}",
                    env,
                    dir_name,
                )
