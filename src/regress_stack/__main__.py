import argparse
import logging
import pathlib
import subprocess
import typing
from pprint import pprint

import regress_stack.modules
from regress_stack.core import utils
from regress_stack.core.modules import get_execution_order, modules
from regress_stack.modules import keystone

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
def test():
    env = keystone.auth_env()
    dir_name = "mycloud01"
    utils.run("tempest", ["init", dir_name])
    utils.run(
        "discover-tempest-config",
        [
            "--create",
            "--flavor-min-mem",
            "128",
            "--image",
            "http://download.cirros-cloud.net/0.6.3/cirros-0.6.3-x86_64-disk.img",
        ],
        env=env,
        cwd=dir_name,
    )
    try:
        utils.run("tempest", ["run", "--smoke"], env=env, cwd=dir_name)
    except subprocess.CalledProcessError:
        # silence to fail on the next command
        pass
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

    subparsers.add_parser("test", help="Run the tests.")

    subparsers.add_parser("list-modules", help="List available modules.")

    args = parser.parse_args()

    logging.basicConfig(level=logging.DEBUG)

    if args.command == "plan":
        plan(args.target)
    elif args.command == "setup":
        setup(args.target)
    elif args.command == "test":
        test()
    elif args.command == "list-modules":
        list_modules()


if __name__ == "__main__":
    main()
