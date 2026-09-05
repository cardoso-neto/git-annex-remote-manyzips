"""End-to-end tests against the real git-annex executable."""

# pylint: disable=missing-class-docstring,missing-function-docstring
# pylint: disable=consider-using-with

import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

HAS_TOOLS = all(shutil.which(command) for command in ("git", "git-annex"))


@unittest.skipUnless(HAS_TOOLS, "git and git-annex are required")
class GitAnnexIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.repo = self.root / "repository with spaces"
        self.remote_directory = self.root / "remote with spaces"
        self.bin_directory = self.root / "bin"
        self.repo.mkdir()
        self.bin_directory.mkdir()

        launcher = self.bin_directory / "git-annex-remote-manyzips"
        launcher.write_text(
            "#!/bin/sh\n"
            f'exec "{sys.executable}" -m git_annex_remote_manyzips.manyzips "$@"\n'
        )
        launcher.chmod(0o755)
        self.environment = os.environ.copy()
        self.environment["PATH"] = (
            f"{self.bin_directory}{os.pathsep}{self.environment['PATH']}"
        )

        self.run_command("git", "init", "--quiet")
        self.run_command("git", "config", "user.name", "ManyZips Tests")
        self.run_command("git", "config", "user.email", "manyzips@example.invalid")
        self.run_command("git", "annex", "init", "--quiet", "test-repository")

    def run_command(self, *command):
        result = subprocess.run(
            command,
            cwd=self.repo,
            env=self.environment,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        if result.returncode:
            self.fail(
                f"Command failed ({result.returncode}): {' '.join(command)}\n"
                f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
            )
        return result

    def init_remote(self, address_length="3", compression="deflate"):
        self.run_command(
            "git",
            "annex",
            "initremote",
            "archive",
            "type=external",
            "externaltype=manyzips",
            "encryption=none",
            f"address_length={address_length}",
            f"compression={compression}",
            f"directory={self.remote_directory}",
        )

    def test_round_trip_fsck_and_remote_removal(self):
        files = {
            "empty.bin": b"",
            "text file--with spaces.txt": b"compress me\n" * 500,
            "binary.bin": bytes(range(256)) * 20,
        }
        for name, payload in files.items():
            (self.repo / name).write_bytes(payload)
        expected_hashes = {
            name: hashlib.sha256(payload).hexdigest() for name, payload in files.items()
        }

        self.run_command("git", "annex", "add", "--quiet", ".")
        self.init_remote()
        self.run_command("git", "annex", "copy", "--jobs=4", "--to", "archive")
        self.run_command("git", "annex", "fsck", "--from", "archive")

        archives = list(self.remote_directory.glob("*.zip"))
        self.assertTrue(archives)
        for archive_path in archives:
            with ZipFile(archive_path) as archive:
                for info in archive.infolist():
                    self.assertEqual(info.compress_type, ZIP_DEFLATED)

        self.run_command("git", "annex", "drop", "--force", ".")
        self.run_command("git", "annex", "copy", "--jobs=4", "--from", "archive")
        for name, expected_hash in expected_hashes.items():
            self.assertEqual(
                hashlib.sha256((self.repo / name).read_bytes()).hexdigest(),
                expected_hash,
            )

        removed_name = "text file--with spaces.txt"
        self.run_command(
            "git",
            "annex",
            "drop",
            "--force",
            "--from",
            "archive",
            "--",
            removed_name,
        )
        whereis = self.run_command("git", "annex", "whereis", "--json", removed_name)
        self.assertNotIn(str(self.remote_directory), whereis.stdout)

    def test_concurrent_stores_targeting_one_archive(self):
        payloads = []
        candidate = 0
        while len(payloads) < 24:
            payload = f"collision candidate {candidate}\n".encode()
            if hashlib.sha256(payload).hexdigest().startswith("a"):
                payloads.append(payload)
            candidate += 1

        for index, payload in enumerate(payloads):
            (self.repo / f"file-{index:02}.txt").write_bytes(payload)
        self.run_command("git", "annex", "add", "--quiet", ".")
        self.init_remote(address_length="1", compression="store")

        self.run_command("git", "annex", "copy", "--jobs=8", "--to", "archive")

        archive_path = self.remote_directory / "a.zip"
        with ZipFile(archive_path) as archive:
            self.assertEqual(len(archive.infolist()), len(payloads))
            self.assertIsNone(archive.testzip())
        self.run_command("git", "annex", "fsck", "--jobs=8", "--from", "archive")


if __name__ == "__main__":
    unittest.main()
