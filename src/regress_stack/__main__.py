# Copyright 2025 - Canonical Ltd
# SPDX-License-Identifier: GPL-3.0-only

import click
import logging
import os
import pathlib
import subprocess
from pprint import pprint

import regress_stack.modules
from regress_stack.core import utils
from regress_stack.core.modules import get_execution_order, modules
from regress_stack.modules import keystone
from regress_stack.modules import utils as module_utils

LOG = logging.getLogger(__name__)


def _output_log_file(path: pathlib.Path):
    """Output the contents of a log file to stdout."""
    with path.open() as log_file:
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


@click.group(
    name="openstack-deb-tester",
    help="A CLI tool for testing OpenStack Debian packages.",
)
def main():
    """OpenStack Debian package testing tool."""
    logging.basicConfig(level=logging.DEBUG)


@main.command()
@click.argument("target", required=False)
def plan(target):
    """Plan the test execution order for modules."""
    order = get_execution_order(regress_stack.modules, target)
    print("Execution Order:")
    pprint(order)


@main.command()
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


@main.command()
@click.option(
    "--concurrency",
    type=str,
    default="1",
    callback=lambda ctx, param, value: utils.concurrency_cb(value)
    if value != "1"
    else 1,
    help="The number of workers to use, defaults to 1. The value 'auto' sets concurrency to number of cpus / 3.",
)
@utils.measure_time
def test(concurrency):
    """Run the regression tests using Tempest."""
    env = os.environ.copy()
    env.update(keystone.auth_env())
    dir_name = "mycloud01"
    release = utils.release()
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

    try:
        with utils.banner("Fetching failing tests"):
            utils.run("stestr", ["failing", "--list"], cwd=dir_name)
    except subprocess.CalledProcessError:
        collect_logs()
        raise


@main.command("list-modules")
def list_modules():
    """List all available modules in the system."""
    _ = get_execution_order(regress_stack.modules)
    for module in modules():
        print(module)


if __name__ == "__main__":
    main()
