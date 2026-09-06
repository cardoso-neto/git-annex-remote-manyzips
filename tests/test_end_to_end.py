"""End-to-end tests that exercise the remote through real git-annex commands."""

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
from zipfile import ZIP_DEFLATED, ZIP_LZMA, ZIP_STORED, ZipFile


class GitAnnexEndToEndTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        missing = [
            command for command in ("git", "git-annex") if not shutil.which(command)
        ]
        if missing:
            raise RuntimeError(
                f"Required test commands not found: {', '.join(missing)}"
            )

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.repo = self.root / "repository with spaces"
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

    def run_command(self, *command, expected=(0,)):
        result = subprocess.run(
            command,
            cwd=self.repo,
            env=self.environment,
            capture_output=True,
            text=True,
            timeout=90,
            check=False,
        )
        if result.returncode not in expected:
            self.fail(
                f"Command returned {result.returncode}, expected {expected}: "
                f"{' '.join(command)}\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
            )
        return result

    def remote_directory(self, name="archive"):
        return self.root / f"{name} remote with spaces"

    def init_remote(
        self,
        name="archive",
        *,
        directory=True,
        address_length="3",
        compression="deflate",
        expected=(0,),
    ):
        options = [
            "type=external",
            "externaltype=manyzips",
            "encryption=none",
        ]
        if address_length is not None:
            options.append(f"address_length={address_length}")
        if compression is not None:
            options.append(f"compression={compression}")
        if directory is True:
            directory = self.remote_directory(name)
        if directory is not None:
            options.append(f"directory={directory}")
        return self.run_command(
            "git", "annex", "initremote", name, *options, expected=expected
        )

    def annex_files(self, files):
        for name, payload in files.items():
            path = self.repo / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(payload)
        self.run_command("git", "annex", "add", "--quiet", "--backend=SHA256E", ".")

    def key_for(self, name):
        return self.run_command("git", "annex", "lookupkey", "--", name).stdout.strip()

    @staticmethod
    def archive_for(directory, key, address_length):
        address = key.rsplit("--", 1)[-1][:address_length]
        return directory / f"{address}.zip"

    @staticmethod
    def colliding_payloads(prefix, count):
        payloads = []
        candidate = 0
        while len(payloads) < count:
            payload = f"collision candidate {candidate}\n".encode()
            if hashlib.sha256(payload).hexdigest().startswith(prefix):
                payloads.append(payload)
            candidate += 1
        return payloads

    @staticmethod
    def replace_member(archive_path, key, replacement):
        with ZipFile(archive_path) as source:
            members = [(info, source.read(info.filename)) for info in source.infolist()]
        with ZipFile(archive_path, "w", allowZip64=True) as destination:
            for info, content in members:
                destination.writestr(
                    info, replacement if info.filename == key else content
                )

    def assert_file_contents(self, files):
        for name, expected in files.items():
            with self.subTest(name=name):
                self.assertEqual((self.repo / name).read_bytes(), expected)

    def test_configuration_errors_are_reported_by_git_annex(self):
        nested_directory = self.root / "nested" / "remote" / "directory"
        self.init_remote(
            "defaults",
            directory=nested_directory,
            address_length=None,
            compression=None,
        )
        self.assertTrue(nested_directory.is_dir())

        invalid_options = (
            ("zero_address", {"address_length": "0"}, "address_length"),
            ("large_address", {"address_length": "4"}, "address_length"),
            ("text_address", {"address_length": "many"}, "address_length"),
            ("unknown_compression", {"compression": "brotli"}, "Compression type"),
            ("missing_directory", {"directory": None}, "directory="),
        )
        for name, options, message in invalid_options:
            with self.subTest(name=name):
                result = self.init_remote(name, expected=(1,), **options)
                self.assertIn(message, result.stdout + result.stderr)

        files = {"offline.txt": b"external storage can disappear\n"}
        self.annex_files(files)
        offline_directory = self.remote_directory("offline")
        self.init_remote("offline", directory=offline_directory)
        shutil.rmtree(offline_directory)
        result = self.run_command(
            "git",
            "annex",
            "checkpresentkey",
            self.key_for("offline.txt"),
            "offline",
            expected=(100,),
        )
        self.assertIn("not found", result.stdout + result.stderr)

    def test_every_compression_mode_round_trips_real_content(self):
        files = {"compressible text.txt": (b"many small files\n" * 2000)}
        self.annex_files(files)
        modes = {
            "store": ZIP_STORED,
            "lzma": ZIP_LZMA,
            "deflate": ZIP_DEFLATED,
            "stored": ZIP_STORED,
            "deflated": ZIP_DEFLATED,
        }

        for compression, expected_type in modes.items():
            remote = f"mode_{compression}"
            directory = self.remote_directory(remote)
            with self.subTest(compression=compression):
                self.init_remote(
                    remote,
                    directory=directory,
                    address_length="1",
                    compression=compression,
                )
                self.run_command(
                    "git", "annex", "copy", "--quiet", "--to", remote, "--", *files
                )
                archives = list(directory.glob("*.zip"))
                self.assertEqual(len(archives), 1)
                with ZipFile(archives[0]) as archive:
                    self.assertTrue(archive.infolist())
                    self.assertTrue(
                        all(
                            info.compress_type == expected_type
                            for info in archive.infolist()
                        )
                    )

                self.run_command("git", "annex", "drop", "--quiet", "--force", *files)
                self.run_command(
                    "git", "annex", "copy", "--quiet", "--from", remote, "--", *files
                )
                self.assert_file_contents(files)

    def test_round_trip_fsck_idempotency_and_remote_removal(self):
        files = {
            "empty.bin": b"",
            "documents/notes with spaces.txt": b"compress me\n" * 500,
            "media/binary.bin": bytes(range(256)) * 20,
        }
        self.annex_files(files)
        directory = self.remote_directory()
        self.init_remote(directory=directory)

        self.run_command("git", "annex", "copy", "--jobs=4", "--to", "archive")
        self.run_command("git", "annex", "copy", "--jobs=4", "--to", "archive")
        self.run_command("git", "annex", "fsck", "--from", "archive")

        for archive_path in directory.glob("*.zip"):
            with ZipFile(archive_path) as archive:
                names = archive.namelist()
                self.assertEqual(len(names), len(set(names)))
                self.assertIsNone(archive.testzip())

        self.run_command("git", "annex", "drop", "--quiet", "--force", ".")
        self.run_command("git", "annex", "copy", "--jobs=4", "--from", "archive")
        self.assert_file_contents(files)

        removed_name = "documents/notes with spaces.txt"
        removed_key = self.key_for(removed_name)
        self.run_command(
            "git",
            "annex",
            "drop",
            "--quiet",
            "--force",
            "--from",
            "archive",
            "--",
            removed_name,
        )
        self.run_command(
            "git",
            "annex",
            "checkpresentkey",
            removed_key,
            "archive",
            expected=(1,),
        )

    def test_parallel_transfers_share_an_archive_without_data_loss(self):
        payloads = self.colliding_payloads("a", 24)
        files = {
            f"parallel/file-{index:02}.txt": payload
            for index, payload in enumerate(payloads)
        }
        self.annex_files(files)
        keys = {name: self.key_for(name) for name in files}
        directory = self.remote_directory()
        self.init_remote(directory=directory, address_length="1", compression="store")

        self.run_command("git", "annex", "copy", "--jobs=8", "--to", "archive")
        self.run_command("git", "annex", "copy", "--jobs=8", "--to", "archive")
        archive_path = directory / "a.zip"
        with ZipFile(archive_path) as archive:
            self.assertEqual(set(archive.namelist()), set(keys.values()))
            self.assertIsNone(archive.testzip())

        self.run_command("git", "annex", "fsck", "--jobs=8", "--from", "archive")
        self.run_command("git", "annex", "drop", "--quiet", "--force", ".")
        self.run_command("git", "annex", "copy", "--jobs=8", "--from", "archive")
        self.assert_file_contents(files)

        removed_name = next(iter(files))
        self.run_command(
            "git",
            "annex",
            "drop",
            "--quiet",
            "--force",
            "--from",
            "archive",
            "--",
            removed_name,
        )
        with ZipFile(archive_path) as archive:
            self.assertNotIn(keys[removed_name], archive.namelist())
            self.assertEqual(
                set(archive.namelist()), set(keys.values()) - {keys[removed_name]}
            )
            self.assertIsNone(archive.testzip())

    def test_damaged_member_is_rejected_without_harming_its_neighbors(self):
        payloads = self.colliding_payloads("b", 2)
        files = {
            "damaged/first.bin": payloads[0],
            "damaged/second.bin": payloads[1],
        }
        self.annex_files(files)
        keys = {name: self.key_for(name) for name in files}
        directory = self.remote_directory()
        self.init_remote(directory=directory, address_length="1", compression="deflate")
        self.run_command("git", "annex", "copy", "--to", "archive")

        damaged_name, healthy_name = files
        damaged_key = keys[damaged_name]
        healthy_key = keys[healthy_name]
        archive_path = directory / "b.zip"
        self.replace_member(archive_path, damaged_key, b"truncated")

        self.run_command(
            "git",
            "annex",
            "checkpresentkey",
            damaged_key,
            "archive",
            expected=(1,),
        )
        self.run_command("git", "annex", "checkpresentkey", healthy_key, "archive")
        with ZipFile(archive_path) as archive:
            self.assertNotIn(damaged_key, archive.namelist())
            self.assertIn(healthy_key, archive.namelist())
            self.assertIsNone(archive.testzip())

        self.run_command(
            "git", "annex", "drop", "--quiet", "--force", "--", healthy_name
        )
        self.run_command(
            "git", "annex", "copy", "--quiet", "--from", "archive", "--", healthy_name
        )
        self.assertEqual((self.repo / healthy_name).read_bytes(), files[healthy_name])

    def test_corrupt_archive_returns_a_protocol_error(self):
        files = {"corruption/input.dat": b"remote media can become corrupt\n" * 20}
        self.annex_files(files)
        key = self.key_for("corruption/input.dat")
        directory = self.remote_directory()
        self.init_remote(directory=directory, address_length="1")
        self.run_command("git", "annex", "copy", "--to", "archive")

        archive_path = self.archive_for(directory, key, 1)
        archive_path.write_bytes(b"this is not a zip archive")
        result = self.run_command(
            "git",
            "annex",
            "checkpresentkey",
            key,
            "archive",
            expected=(100,),
        )
        output = result.stdout + result.stderr
        self.assertIn("Could not check", output)
        self.assertNotIn("Traceback", output)


if __name__ == "__main__":
    unittest.main()
