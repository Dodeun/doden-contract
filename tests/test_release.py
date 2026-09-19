"""The release script, run rather than read.

`release.sh` is the one thing here that publishes, and the mistake it exists
to prevent - pushing the exact version and forgetting to move the major - is
invisible until something downstream loads the wrong commit. So its git
mechanics are exercised against a real bare remote in `release-mechanics.sh`,
including that mistake, and this file is what makes that run with everything
else instead of when somebody remembers.

Two of the three ways it was wrong were found here rather than by reading it:
`git ls-remote --tags origin v1` returns the *tag object* for an annotated
tag, and passing the short name filters the peeled `refs/tags/v1^{}` line out
entirely, so there is nothing left to prefer.
"""

import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RELEASE = ROOT / "release.sh"
BASH = shutil.which("bash")


class TheReleaseScript(unittest.TestCase):
    def test_it_is_there_and_it_is_executable_by_git(self):
        """A release script somebody has to remember to `chmod +x` is a
        release script that fails on a fresh clone. Git tracks the bit; this
        asserts git agrees, not that the filesystem does - on Windows the
        filesystem has no opinion."""
        self.assertTrue(RELEASE.is_file())
        listing = subprocess.run(
            ["git", "ls-files", "-s", "release.sh"],
            cwd=ROOT,
            capture_output=True,
            text=True,
        )
        self.assertTrue(listing.stdout.startswith("100755"), listing.stdout)

    @unittest.skipUnless(BASH, "no bash on PATH")
    def test_it_parses(self):
        parsed = subprocess.run(
            [BASH, "-n", str(RELEASE)], capture_output=True, text=True
        )
        self.assertEqual(0, parsed.returncode, parsed.stderr)

    @unittest.skipUnless(BASH, "no bash on PATH")
    def test_the_git_mechanics_hold_against_a_real_remote(self):
        lab = subprocess.run(
            [BASH, str(ROOT / "tests" / "release-mechanics.sh")],
            capture_output=True,
            text=True,
        )
        self.assertEqual(0, lab.returncode, lab.stdout + lab.stderr)
        self.assertIn("all mechanics hold", lab.stdout)
