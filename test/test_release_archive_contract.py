from __future__ import annotations

import subprocess
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
REQUIRED_PACKAGE_INITIALIZERS = (
    "api/__init__.py",
    "services/__init__.py",
    "services/protocol/__init__.py",
    "services/storage/__init__.py",
)


class ReleaseArchiveContractTests(unittest.TestCase):
    def test_required_package_initializers_are_tracked_for_git_archives(self):
        tracked = set(
            subprocess.check_output(
                ["git", "ls-files", "--", *REQUIRED_PACKAGE_INITIALIZERS],
                cwd=PROJECT_ROOT,
                text=True,
            ).splitlines()
        )
        self.assertSetEqual(tracked, set(REQUIRED_PACKAGE_INITIALIZERS))


if __name__ == "__main__":
    unittest.main()
