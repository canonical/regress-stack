# Copyright 2025 - Canonical Ltd
# SPDX-License-Identifier: GPL-3.0-only

import contextlib
import functools
import ipaddress
import logging
import math
import multiprocessing
import os
import pathlib
import platform
import socket
import subprocess
import time
import typing

import pyroute2

LOG = logging.getLogger(__name__)

REGRESS_STACK_DIR = pathlib.Path("/var/lib/regress-stack/")


@contextlib.contextmanager
def measure(section: str):
    start = time.time()
    try:
        yield
    finally:
        end = time.time()
        LOG.info("%s: %.2fs", section, end - start)


def print_ascii_banner(msg: str):
    width = 80
    print("#" * width)
    print(msg.center(width, "#"))
    print("#" * width)


@contextlib.contextmanager
def banner(msg: str):
    print_ascii_banner("START " + msg)
    yield
    print_ascii_banner("END " + msg)


def measure_time(func):
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        with measure("Function " + func.__name__):
            return func(*args, **kwargs)

    return wrapper


def run(
    cmd: str,
    args: typing.Sequence[str] = (),
    env: typing.Optional[typing.Dict[str, str]] = None,
    cwd: typing.Optional[str] = None,
) -> str:
    cmd_args = [cmd]
    cmd_args.extend(args)
    try:
        result = subprocess.run(
            cmd_args,
            shell=False,
            check=True,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
            cwd=cwd,
        )
    except subprocess.CalledProcessError as e:
        LOG.error("Command %r failed with exit code %d", cmd, e.returncode)
        LOG.error("Command %r stdout: %s", cmd, e.stdout)
        LOG.error("Command %r stderr: %s", cmd, e.stderr)
        raise e
    LOG.debug(
        "Command %r stdout: %s, stderr: %s",
        " ".join(cmd_args),
        result.stdout,
        result.stderr,
    )
    return result.stdout


def system(
    cmd: str,
    env: typing.Optional[typing.Dict[str, str]] = None,
    cwd: typing.Optional[str] = None,
) -> int:
    exit_code = -1
    saved_env = os.environ
    saved_cwd = os.getcwd()

    # NOTE: os.system does not check nor raise on non-zero return code from
    # command.
    #
    # We do try/finally for consistency and in the event we add handling of
    # OSError level exceptions at some point in the future.
    try:
        if env:
            os.environ.update(env)
        if cwd:
            os.chdir(cwd)
        status = os.system(cmd)
        exit_code = -1
        # See https://github.com/python/cpython/pull/19201 for background.
        if os.WIFSIGNALED(status):
            exit_code = -os.WTERMSIG(status)
        elif os.WIFEXITED(status):
            exit_code = os.WEXITSTATUS(status)
        elif os.WIFSTOPPED(status):
            exit_code = -os.WSTOPSIG(status)
    finally:
        if env:
            os.environ = saved_env
        if cwd:
            os.chdir(saved_cwd)

    return exit_code


def sudo(
    cmd: str, args: typing.Sequence[str], user: typing.Optional[str] = None
) -> str:
    opts = []
    if user:
        opts = ["--user", user]
    return run("sudo", opts + [cmd, *args])


def restart_service(service: str):
    run("systemctl", ["restart", service])


def restart_apache():
    restart_service("apache2")


@functools.lru_cache()
def fqdn() -> str:
    return run("hostname", ["-f"]).strip()


@functools.lru_cache()
def _get_local_ip_by_default_route() -> typing.Tuple[str, int]:
    """Get host IP from default route interface."""
    with pyroute2.NDB() as ndb:
        default_route_ifindex = ndb.routes["default"]["oif"]
        iface = ndb.interfaces[default_route_ifindex]
        ipaddr = iface.ipaddr[socket.AF_INET]
        return ipaddr["address"], ipaddr["prefixlen"]


@functools.lru_cache()
def my_ip() -> str:
    try:
        return _get_local_ip_by_default_route()[0]
    except Exception:
        LOG.exception("Failed to get local IP by default route")
        return "127.0.0.1"


@functools.lru_cache()
def my_network() -> str:
    try:
        ipaddr = _get_local_ip_by_default_route()
        return str(ipaddress.ip_network(f"{ipaddr[0]}/{ipaddr[1]}", strict=False))
    except Exception:
        LOG.exception("Failed to get local IP by default route")
        return "127.0.0.1/8"


def exists_cache(path: pathlib.Path):
    """Wrapped function is not executed if resulting file exists."""

    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            if path.exists():
                return path
            result = func(*args, **kwargs)
            return result

        return wrapper

    return decorator


def machine() -> str:
    """Return machine type."""
    machine_name = platform.machine().lower()
    if machine_name == "x86_64":
        return "amd64"
    elif machine_name == "aarch64":
        return "arm64"
    elif machine_name == "powerpc":
        return "ppc64el"
    return machine_name


def release() -> str:
    """Return release name."""
    try:
        return platform.freedesktop_os_release()["VERSION_CODENAME"]
    except Exception:
        LOG.exception("Failed to get release name")
        return "noble"


def mark_setup(name: str):
    """Mark task as done."""
    REGRESS_STACK_DIR.mkdir(parents=True, exist_ok=True)
    done_file = REGRESS_STACK_DIR / (name + ".setup")
    done_file.touch()
    return done_file


def is_setup_done(name: str) -> bool:
    """Check if task is done."""
    done_file = REGRESS_STACK_DIR / (name + ".setup")
    return done_file.exists()


def concurrency_cb(arg: str) -> int:
    """Handle concurrency argument, for use with ArgumentParser.

    :raises: ValueError.
    """
    if arg == "auto":
        return math.ceil(multiprocessing.cpu_count() / 3)
    return int(arg)
