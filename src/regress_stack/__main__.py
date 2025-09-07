# Copyright 2025 - Canonical Ltd
# SPDX-License-Identifier: GPL-3.0-only

import argparse
import logging
import os
import pathlib
import subprocess
import typing
from pprint import pprint

import regress_stack.modules
from regress_stack.core import utils
from regress_stack.core.modules import get_execution_order, modules
from regress_stack.modules import keystone
from regress_stack.modules import utils as module_utils

LOG = logging.getLogger(__name__)


def plan(target: typing.Optional[str]):
    order = get_execution_order(regress_stack.modules, target)
    print(
        "Execution Order:",
    )
    pprint(order)


@utils.measure_time
def setup(target: str):
    try:
        for mod in get_execution_order(regress_stack.modules, target):
            if setup := getattr(mod.module, "setup", None):
                with utils.measure("setup " + mod.name):
                    setup()
                    utils.mark_setup(mod.name)
    except Exception as e:
        LOG.error("Failed to setup %s: %s", target, e)
        collect_logs()
        raise


def _output_log_file(path: pathlib.Path):
    with path.open() as log_file:
        for line in log_file:
            print(line, end="")


def collect_logs():
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


@utils.measure_time
def test(concurrency: int):
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
        ("validation", "connect_method", "floating"),
        ("network-feature-enabled", "port_security", "true"),
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


def list_modules():
    _ = get_execution_order(regress_stack.modules)
    for module in modules():
        print(module)


def main():
    parser = argparse.ArgumentParser(
        prog="openstack-deb-tester",
        description="A CLI tool for testing OpenStack Debian packages.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    def add_common_arguments(subparser):
        subparser.add_argument("target", nargs="?", help="Target to test (optional).")

    parser_plan = subparsers.add_parser("plan", help="Plan the test execution.")
    add_common_arguments(parser_plan)

    parser_setup = subparsers.add_parser("setup", help="Execute the tests.")
    add_common_arguments(parser_setup)

    parser_test = subparsers.add_parser("test", help="Run the tests.")
    parser_test.add_argument(
        "--concurrency",
        nargs="?",
        type=utils.concurrency_cb,
        help=(
            "The number of workers to use, defaults to 1.  The value auto sets concurrency to number of cpus / 3."
        ),
    )

    subparsers.add_parser("list-modules", help="List available modules.")

    args = parser.parse_args()

    logging.basicConfig(level=logging.DEBUG)

    if args.command == "plan":
        plan(args.target)
    elif args.command == "setup":
        setup(args.target)
    elif args.command == "test":
        test(concurrency=args.concurrency or 1)
    elif args.command == "list-modules":
        list_modules()


if __name__ == "__main__":
    main()
