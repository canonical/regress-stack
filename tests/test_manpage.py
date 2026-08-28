# SPDX-License-Identifier: GPL-3.0-only

from pathlib import Path

ROOT = Path(__file__).parents[1]
MANPAGE = ROOT / "debian" / "regress-stack.1"


def test_manpage_documents_every_cli_command():
    text = MANPAGE.read_text(encoding="utf-8")

    for command in (
        "list-modules",
        "packages",
        "plan",
        "playground",
        "setup",
        "test",
    ):
        assert command in text


def test_debian_package_installs_manpage():
    install_list = ROOT / "debian" / "python3-regress-stack.manpages"

    assert install_list.read_text(encoding="utf-8").splitlines() == [
        "debian/regress-stack.1"
    ]
