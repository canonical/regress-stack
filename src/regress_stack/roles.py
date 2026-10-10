# Copyright 2026 - Canonical Ltd
# SPDX-License-Identifier: GPL-3.0-only

"""Service-local roles for explicit deployments."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Literal

from regress_stack.core.deployment import Context


@dataclass(frozen=True)
class Role:
    """Local prerequisites, package selection and configuration ownership.

    Optional ordering applies only to enabled local modules. Shared
    dependencies are supplied by other nodes and do not add local packages.
    Package callbacks receive the context even before it has been activated.
    """

    requires: tuple[str, ...] = ()
    after: tuple[str, ...] = ()
    shared_dependencies: tuple[str, ...] = ()
    packages: tuple[str, ...] | Callable[[Context, bool], Sequence[str]] | None = None
    configured_by: Literal["module", "recipe"] = "module"
    setup: Callable[[], None] | None = None
