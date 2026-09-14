# Copyright 2026 - Canonical Ltd
# SPDX-License-Identifier: GPL-3.0-only

"""Validated fixed deployments and recipient-scoped setup state.

This module has no machine configuration or transport side effects. Inventories
are public; generated preseeds are secrets and must be transferred by the caller.
"""

import contextlib
import contextvars
import dataclasses
import ipaddress
import json
import os
from pathlib import Path
import re
import stat
import tempfile
from typing import Iterator, Mapping, Optional
import uuid


_NAME = re.compile(r"[a-z](?:[a-z0-9-]{0,61}[a-z0-9])?\Z")
_INTERFACE = re.compile(r"[a-zA-Z0-9_.-]{1,15}\Z")


def _ipv4(value: str) -> str:
    if not isinstance(value, str):
        raise ValueError("IPv4 addresses must be strings")
    address = ipaddress.IPv4Address(value)
    if address.is_unspecified or address.is_multicast or address.is_loopback:
        raise ValueError(
            "Node and endpoint addresses must be unicast, non-loopback IPv4"
        )
    return str(address)


@dataclasses.dataclass(frozen=True)
class Node:
    name: str
    address: str
    management_interface: str
    provider_interface: str

    def __post_init__(self):
        if not isinstance(self.name, str) or not _NAME.fullmatch(self.name):
            raise ValueError("Node names must be lowercase DNS labels")
        _ipv4(self.address)
        for interface in (self.management_interface, self.provider_interface):
            if not isinstance(interface, str) or not _INTERFACE.fullmatch(interface):
                raise ValueError("Invalid interface name")
        if self.management_interface == self.provider_interface:
            raise ValueError("Provider and management interfaces must be distinct")


@dataclasses.dataclass(frozen=True)
class ProviderNetwork:
    cidr: str
    gateway: str
    allocation_start: str
    allocation_end: str

    def __post_init__(self):
        network = ipaddress.IPv4Network(self.cidr)
        addresses = [
            ipaddress.IPv4Address(_ipv4(value))
            for value in (self.gateway, self.allocation_start, self.allocation_end)
        ]
        if any(
            value not in network
            or value in (network.network_address, network.broadcast_address)
            for value in addresses
        ):
            raise ValueError(
                "Provider gateway and allocation range must be usable subnet addresses"
            )
        gateway, start, end = addresses
        if start > end or start <= gateway <= end:
            raise ValueError(
                "Provider allocation range must be ordered and exclude the gateway"
            )


@dataclasses.dataclass(frozen=True)
class Deployment:
    profile: str
    controllers: tuple[Node, ...]
    computes: tuple[Node, ...]
    api_address: str
    management_cidr: str
    provider: ProviderNetwork
    schema: int = 1

    def __post_init__(self):
        if type(self.schema) is not int or self.schema != 1:
            raise ValueError("Unsupported inventory schema")
        expected = {"single": 1, "hyperconverged": 3}.get(self.profile)
        if expected is None or len(self.controllers) != expected:
            raise ValueError("Profiles require exactly one or three controllers")
        nodes = self.nodes
        if len({node.name for node in nodes}) != len(nodes):
            raise ValueError("Node names must be unique")
        if len({node.address for node in nodes}) != len(nodes):
            raise ValueError("Node addresses must be unique")
        management = ipaddress.IPv4Network(self.management_cidr)
        for address in [node.address for node in nodes] + [self.api_address]:
            ip = ipaddress.IPv4Address(_ipv4(address))
            if ip not in management or ip in (
                management.network_address,
                management.broadcast_address,
            ):
                raise ValueError(
                    "Management addresses must be usable addresses on the shared subnet"
                )
        if management.overlaps(ipaddress.IPv4Network(self.provider.cidr)):
            raise ValueError("Management and provider subnets must not overlap")
        if self.profile == "hyperconverged":
            if self.api_address in {node.address for node in nodes}:
                raise ValueError(
                    "The API VIP must be reserved separately from node addresses"
                )
        elif self.api_address != self.controllers[0].address:
            raise ValueError("The single-controller endpoint must use its node address")

    @property
    def nodes(self) -> tuple[Node, ...]:
        return self.controllers + self.computes

    @property
    def bootstrap(self) -> Node:
        return self.controllers[0]

    def node(self, name: str) -> Node:
        for node in self.nodes:
            if node.name == name:
                return node
        raise ValueError("Local node is not in the deployment inventory")

    def role(self, name: str) -> str:
        node = self.node(name)
        return "controller" if node in self.controllers else "compute"

    @classmethod
    def from_dict(cls, data: dict) -> "Deployment":
        try:
            return cls(
                **{
                    **data,
                    "controllers": tuple(Node(**node) for node in data["controllers"]),
                    "computes": tuple(Node(**node) for node in data["computes"]),
                    "provider": ProviderNetwork(**data["provider"]),
                }
            )
        except (TypeError, KeyError, AttributeError) as exc:
            raise ValueError("Invalid deployment inventory structure") from exc

    @classmethod
    def read(cls, path: Path) -> "Deployment":
        return cls.from_dict(json.loads(path.read_text()))


@dataclasses.dataclass(frozen=True, repr=False)
class Secret:
    value: str
    recipients: frozenset[str]

    def __repr__(self):
        return "Secret(<redacted>)"


@dataclasses.dataclass(repr=False)
class Context:
    deployment: Deployment
    local_name: str
    deployment_id: str
    values: dict[str, str] = dataclasses.field(default_factory=dict, repr=False)

    def __post_init__(self):
        self.deployment.node(self.local_name)
        uuid.UUID(self.deployment_id)
        if not isinstance(self.values, dict) or any(
            not isinstance(key, str) or not isinstance(value, str)
            for key, value in self.values.items()
        ):
            raise ValueError("Preseed values must be string pairs")

    def __repr__(self):
        return f"Context(local_name={self.local_name!r}, values=<redacted>)"

    @property
    def local(self) -> Node:
        return self.deployment.node(self.local_name)

    @property
    def controller(self) -> bool:
        return self.deployment.role(self.local_name) == "controller"

    @property
    def bootstrap(self) -> bool:
        return self.local == self.deployment.bootstrap

    def secret(self, key: str) -> str:
        try:
            return self.values[key]
        except KeyError:
            raise ValueError(f"Required preseed value missing: {key}") from None

    def export(self, directory: Path, contributions: Mapping[str, Secret]) -> None:
        """Export all peer preseeds. Modules explicitly name each secret's recipients."""
        if not self.bootstrap:
            raise ValueError("Only the bootstrap node can export join preseeds")
        names = {node.name for node in self.deployment.nodes}
        for secret in contributions.values():
            if not isinstance(secret.value, str) or not secret.recipients <= names:
                raise ValueError("Invalid secret contribution")
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        info = directory.lstat()
        if (
            not stat.S_ISDIR(info.st_mode)
            or info.st_mode & 0o077
            or info.st_uid != os.geteuid()
        ):
            raise ValueError(
                "Preseed directory must be owned by the current user with mode 0700"
            )
        for node in self.deployment.nodes:
            if node == self.local:
                continue
            payload = {
                "schema": 1,
                "deployment": dataclasses.asdict(self.deployment),
                "deployment_id": self.deployment_id,
                "local_name": node.name,
                "values": {
                    key: secret.value
                    for key, secret in contributions.items()
                    if node.name in secret.recipients
                },
            }
            private_write(
                directory / f"{node.name}.json", json.dumps(payload, indent=2) + "\n"
            )

    @classmethod
    def read(cls, path: Path) -> "Context":
        data = json.loads(private_read(path))
        if (
            not isinstance(data, dict)
            or set(data)
            != {"schema", "deployment", "deployment_id", "local_name", "values"}
            or type(data["schema"]) is not int
            or data["schema"] != 1
        ):
            raise ValueError("Invalid preseed schema")
        try:
            return cls(
                Deployment.from_dict(data["deployment"]),
                data["local_name"],
                data["deployment_id"],
                data["values"],
            )
        except (TypeError, AttributeError) as exc:
            raise ValueError("Invalid preseed structure") from exc


def private_write(path: Path, content: str) -> None:
    """Publish a complete file atomically, with mode 0600 from creation."""
    fd, temporary = tempfile.mkstemp(prefix=".preseed-", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def private_read(path: Path) -> str:
    """Reject symlinks, non-regular files and credentials readable by others."""
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd) as stream:
        info = os.fstat(stream.fileno())
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_mode & 0o077
            or info.st_uid != os.geteuid()
        ):
            raise ValueError(
                "Preseed must be a private regular file owned by the current user"
            )
        return stream.read()


_CURRENT: contextvars.ContextVar[Optional[Context]] = contextvars.ContextVar(
    "deployment", default=None
)


def current() -> Optional[Context]:
    return _CURRENT.get()


_BOOTSTRAP_ONLY: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "bootstrap_only", default=True
)


def bootstrap_only() -> bool:
    return _BOOTSTRAP_ONLY.get()


@contextlib.contextmanager
def activate(context: Context, *, bootstrap_only: bool = True) -> Iterator[Context]:
    token = _CURRENT.set(context)
    phase_token = _BOOTSTRAP_ONLY.set(bootstrap_only)
    try:
        yield context
    finally:
        _BOOTSTRAP_ONLY.reset(phase_token)
        _CURRENT.reset(token)
