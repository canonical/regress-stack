# Copyright 2026 - Canonical Ltd
# SPDX-License-Identifier: GPL-3.0-only

"""Workaround for the old Noble Manila plugin's Python 3.12 assertion failure.

The metadata filtering test calls assertDictContainsSubset, which Python 3.12
removed. Apply the upstream replacement while retaining the subset comparison.
The Spread task invokes this only for Noble's apt plugin. Updated sources
already contain the replacement and require no edits.

https://opendev.org/openstack/manila-tempest-plugin/commit/c1f3f37990840be797927c826fc638a661ebb5f6
https://docs.python.org/3.12/whatsnew/3.12.html#unittest
"""

import importlib.util
import pathlib
import unittest


def main():
    if hasattr(unittest.TestCase, "assertDictContainsSubset"):
        return
    plugin = importlib.util.find_spec("manila_tempest_tests")
    if plugin is None or plugin.origin is None:
        raise RuntimeError("The apt Manila Tempest plugin is unavailable")
    source = pathlib.Path(plugin.origin).parent / "tests/api/test_shares_actions.py"
    contents = source.read_text()
    old = (
        "self.assertDictContainsSubset(\n"
        "                filters['metadata'], share['metadata'])"
    )
    new = (
        "self.assertLessEqual(\n"
        "                filters['metadata'].items(), share['metadata'].items())"
    )
    if old in contents:
        source.write_text(contents.replace(old, new))
        print("Applied upstream Manila metadata assertion compatibility fix")
    elif "assertDictContainsSubset" in contents:
        raise RuntimeError(
            f"Deprecated assertDictContainsSubset remains in {source}, "
            "but the upstream replacement pattern did not match"
        )
    else:
        print("Manila metadata assertions are already compatible with Python 3.12")


if __name__ == "__main__":
    main()
