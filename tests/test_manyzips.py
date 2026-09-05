"""Unit and protocol tests for the manyzips remote."""

# pylint: disable=missing-class-docstring,missing-function-docstring
# pylint: disable=protected-access,consider-using-with
# pylint: disable=too-many-public-methods

import io
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from zipfile import ZIP_DEFLATED, ZIP_LZMA, ZIP_STORED, ZipFile

from annexremote import RemoteError

from git_annex_remote_manyzips.manyzips import (
    ManyZips,
    UnsupportedCompression,
    _mkdir,
    archive_lock,
    copyfileobj,
    delete_from_zip,
)


class FakeAnnex:
    def __init__(self, configs=None):
        self.configs = dict(configs or {})
        self.config_updates = []
        self.progress_updates = []

    def getconfig(self, name):
        return self.configs.get(name, "")

    def setconfig(self, name, value):
        self.configs[name] = value
        self.config_updates.append((name, value))

    def progress(self, size):
        self.progress_updates.append(size)


class ConfigurationTests(unittest.TestCase):
    def test_address_length_defaults_to_one(self):
        self.assertEqual(ManyZips(FakeAnnex()).address_length, 1)

    def test_address_length_accepts_all_documented_values(self):
        for value in ("1", "2", "3"):
            with self.subTest(value=value):
                remote = ManyZips(FakeAnnex({"address_length": value}))
                self.assertEqual(remote.address_length, int(value))

    def test_address_length_rejects_invalid_values_as_remote_errors(self):
        for value in ("0", "4", "not-a-number"):
            with self.subTest(value=value):
                remote = ManyZips(FakeAnnex({"address_length": value}))
                with self.assertRaisesRegex(RemoteError, "1 to 3"):
                    _ = remote.address_length

    def test_compression_defaults_to_store_and_persists_the_default(self):
        annex = FakeAnnex()
        remote = ManyZips(annex)

        self.assertEqual(remote.compression, "store")
        self.assertEqual(annex.config_updates, [("compression", "store")])

    def test_compression_accepts_canonical_names(self):
        for name in ("store", "lzma", "deflate"):
            with self.subTest(name=name):
                self.assertEqual(
                    ManyZips(FakeAnnex({"compression": name})).compression, name
                )

    def test_compression_normalizes_legacy_documented_names(self):
        for old_name, canonical_name in (
            ("stored", "store"),
            ("deflated", "deflate"),
        ):
            with self.subTest(old_name=old_name):
                annex = FakeAnnex({"compression": old_name})
                self.assertEqual(ManyZips(annex).compression, canonical_name)
                self.assertEqual(
                    annex.config_updates, [("compression", canonical_name)]
                )

    def test_unknown_compression_is_a_remote_error(self):
        remote = ManyZips(FakeAnnex({"compression": "rar"}))
        with self.assertRaises(UnsupportedCompression):
            _ = remote.compression

    def test_directory_is_required(self):
        with self.assertRaisesRegex(RemoteError, "directory="):
            _ = ManyZips(FakeAnnex()).directory

    def test_initremote_creates_nested_directory_and_reports_info(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            target = Path(temp_dir) / "nested" / "remote"
            remote = ManyZips(FakeAnnex({"directory": str(target)}))

            remote.initremote()

            self.assertTrue(target.is_dir())
            self.assertEqual(remote.info["directory"], target.resolve())
            self.assertEqual(remote.info["address_length"], 1)
            self.assertEqual(remote.info["compression"], "store")

    def test_prepare_requires_an_existing_directory(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            missing = Path(temp_dir) / "missing"
            remote = ManyZips(FakeAnnex({"directory": str(missing)}))
            with self.assertRaisesRegex(RemoteError, "not found"):
                remote.prepare()

    def test_prepare_selects_the_compression_algorithm(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            for name, algorithm in (
                ("store", ZIP_STORED),
                ("lzma", ZIP_LZMA),
                ("deflate", ZIP_DEFLATED),
            ):
                with self.subTest(name=name):
                    remote = ManyZips(
                        FakeAnnex({"directory": temp_dir, "compression": name})
                    )
                    remote.prepare()
                    self.assertEqual(remote.compression_algorithm, algorithm)

    def test_mkdir_translates_os_errors(self):
        directory = Mock()
        directory.mkdir.side_effect = OSError("no space")
        with self.assertRaisesRegex(RemoteError, "Failed to write"):
            _mkdir(directory)


class CopyTests(unittest.TestCase):
    def test_copyfileobj_copies_in_chunks_and_reports_cumulative_progress(self):
        source = io.BytesIO(b"abcdefgh")
        destination = io.BytesIO()
        progress = []

        copyfileobj(source, destination, length=3, callback=progress.append)

        self.assertEqual(destination.getvalue(), b"abcdefgh")
        self.assertEqual(progress, [3, 6, 8])

    def test_copyfileobj_handles_empty_files(self):
        destination = io.BytesIO()
        progress = []

        copyfileobj(io.BytesIO(b""), destination, callback=progress.append, file_size=0)

        self.assertEqual(destination.getvalue(), b"")
        self.assertEqual(progress, [])


class ArchiveTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.remote_directory = self.root / "remote directory"
        self.remote_directory.mkdir()

    def make_remote(self, compression="store", address_length="2"):
        annex = FakeAnnex(
            {
                "directory": str(self.remote_directory),
                "compression": compression,
                "address_length": address_length,
            }
        )
        remote = ManyZips(annex)
        remote.prepare()
        return remote, annex

    @staticmethod
    def key(payload, prefix="aa", suffix="data.bin"):
        digest = (prefix + "0" * 64)[:64]
        return f"SHA256E-s{len(payload)}--{digest}.{suffix}"

    def test_store_and_retrieve_round_trip_for_every_compression(self):
        payload = (b"compressible payload\n" * 1000) + bytes(range(256))
        algorithms = {
            "store": ZIP_STORED,
            "lzma": ZIP_LZMA,
            "deflate": ZIP_DEFLATED,
        }
        for index, (compression, algorithm) in enumerate(algorithms.items()):
            with self.subTest(compression=compression):
                remote, annex = self.make_remote(compression)
                source = self.root / f"source {compression}.bin"
                destination = self.root / f"destination {compression}.bin"
                source.write_bytes(payload)
                key = self.key(payload, prefix=f"a{index}")

                remote.transfer_store(key, str(source))
                self.assertTrue(remote.checkpresent(key))
                with ZipFile(remote._get_zip_path(key)) as archive:
                    self.assertEqual(archive.read(key), payload)
                    self.assertEqual(archive.getinfo(key).compress_type, algorithm)
                self.assertEqual(annex.progress_updates[-1], len(payload))

                annex.progress_updates.clear()
                remote.transfer_retrieve(key, str(destination))
                self.assertEqual(destination.read_bytes(), payload)
                self.assertEqual(annex.progress_updates[-1], len(payload))

    def test_empty_file_round_trip(self):
        remote, annex = self.make_remote()
        source = self.root / "empty source"
        destination = self.root / "empty destination"
        source.touch()
        key = self.key(b"", prefix="b0")

        remote.transfer_store(key, str(source))
        remote.transfer_retrieve(key, str(destination))

        self.assertEqual(destination.read_bytes(), b"")
        self.assertEqual(annex.progress_updates, [])

    def test_store_is_idempotent(self):
        remote, annex = self.make_remote()
        source = self.root / "source"
        payload = b"same payload"
        source.write_bytes(payload)
        key = self.key(payload)

        remote.transfer_store(key, str(source))
        first_progress = list(annex.progress_updates)
        remote.transfer_store(key, str(source))

        with ZipFile(remote._get_zip_path(key)) as archive:
            self.assertEqual(archive.namelist().count(key), 1)
        self.assertEqual(annex.progress_updates, first_progress)

    def test_store_replaces_a_wrong_sized_member_without_duplicates(self):
        remote, _ = self.make_remote()
        payload = b"correct payload"
        source = self.root / "source"
        source.write_bytes(payload)
        key = self.key(payload)
        zip_path = remote._get_zip_path(key)
        with ZipFile(zip_path, "w") as archive:
            archive.writestr(key, b"bad")

        remote.transfer_store(key, str(source))

        with ZipFile(zip_path) as archive:
            self.assertEqual(archive.namelist(), [key])
            self.assertEqual(archive.read(key), payload)

    def test_store_failure_removes_the_partial_member(self):
        remote, annex = self.make_remote()
        payload = b"partial payload"
        source = self.root / "source"
        source.write_bytes(payload)
        key = self.key(payload, prefix="ab")

        def fail_progress(_size):
            raise OSError("protocol output closed")

        annex.progress = fail_progress
        with self.assertRaisesRegex(RemoteError, "Could not store"):
            remote.transfer_store(key, str(source))

        with ZipFile(remote._get_zip_path(key)) as archive:
            self.assertNotIn(key, archive.namelist())

    def test_store_accepts_timestamps_before_the_zip_epoch(self):
        remote, _ = self.make_remote()
        source = self.root / "old source"
        payload = b"old"
        source.write_bytes(payload)
        os.utime(source, (1, 1))
        key = self.key(payload, prefix="b1")

        remote.transfer_store(key, str(source))

        with ZipFile(remote._get_zip_path(key)) as archive:
            self.assertEqual(archive.read(key), payload)
            self.assertEqual(archive.getinfo(key).date_time[0], 1980)

    def test_retrieve_failure_preserves_destination_and_removes_temporary_file(self):
        remote, _ = self.make_remote()
        payload = b"stored"
        source = self.root / "source"
        source.write_bytes(payload)
        stored_key = self.key(payload, prefix="cc")
        remote.transfer_store(stored_key, str(source))
        destination = self.root / "destination"
        destination.write_bytes(b"keep me")
        missing_key = self.key(payload, prefix="cc", suffix="missing.bin")

        with self.assertRaisesRegex(RemoteError, "Could not retrieve"):
            remote.transfer_retrieve(missing_key, str(destination))

        self.assertEqual(destination.read_bytes(), b"keep me")
        self.assertEqual(list(self.root.glob(".destination.*.manyzips-temp")), [])

    def test_retrieve_copy_failure_cleans_up_and_preserves_destination(self):
        remote, annex = self.make_remote()
        payload = b"stored payload"
        source = self.root / "source"
        destination = self.root / "destination"
        source.write_bytes(payload)
        destination.write_bytes(b"keep me")
        key = self.key(payload, prefix="cd")
        remote.transfer_store(key, str(source))

        def fail_progress(_size):
            raise OSError("protocol output closed")

        annex.progress = fail_progress
        with self.assertRaisesRegex(RemoteError, "Could not retrieve"):
            remote.transfer_retrieve(key, str(destination))

        self.assertEqual(destination.read_bytes(), b"keep me")
        self.assertEqual(list(self.root.glob(".destination.*.manyzips-temp")), [])

    def test_check_file_sizes_handles_present_missing_and_changed_files(self):
        remote, _ = self.make_remote()
        payload = b"stored payload"
        source = self.root / "source"
        source.write_bytes(payload)
        key = self.key(payload, prefix="ce")

        self.assertFalse(remote.check_file_sizes(key, source))
        remote.transfer_store(key, str(source))
        self.assertTrue(remote.check_file_sizes(key, source))
        source.write_bytes(payload + b" changed")
        self.assertFalse(remote.check_file_sizes(key, source))
        self.assertFalse(remote.check_file_sizes(f"{key}.missing", source))

    def test_checkpresent_deletes_a_wrong_sized_member(self):
        remote, _ = self.make_remote()
        key = self.key(b"expected", prefix="dd")
        zip_path = remote._get_zip_path(key)
        with ZipFile(zip_path, "w") as archive:
            archive.writestr(key, b"short")

        self.assertFalse(remote.checkpresent(key))
        with ZipFile(zip_path) as archive:
            self.assertNotIn(key, archive.namelist())

    def test_checkpresent_accepts_keys_without_embedded_sizes(self):
        remote, _ = self.make_remote()
        key = "GPGHMACSHA1--ee00000000000000000000000000000000000000"
        zip_path = remote._get_zip_path(key)
        with ZipFile(zip_path, "w") as archive:
            archive.writestr(key, b"encrypted")

        self.assertTrue(remote.checkpresent(key))

    def test_checkpresent_reports_a_corrupt_archive_as_remote_error(self):
        remote, _ = self.make_remote()
        key = self.key(b"payload", prefix="ef")
        remote._get_zip_path(key).write_bytes(b"not a zip")

        with self.assertRaisesRegex(RemoteError, "Could not check"):
            remote.checkpresent(key)

    def test_checkpresent_returns_false_when_the_archive_is_missing(self):
        remote, _ = self.make_remote()
        self.assertFalse(remote.checkpresent(self.key(b"missing", prefix="e1")))

    def test_remove_preserves_other_members_and_handles_missing_keys(self):
        remote, _ = self.make_remote(address_length="1")
        first_payload = b"first"
        second_payload = b"second"
        first_source = self.root / "first"
        second_source = self.root / "second"
        first_source.write_bytes(first_payload)
        second_source.write_bytes(second_payload)
        first_key = self.key(first_payload, prefix="a1", suffix="first")
        second_key = self.key(second_payload, prefix="a2", suffix="second")
        remote.transfer_store(first_key, str(first_source))
        remote.transfer_store(second_key, str(second_source))

        remote.remove(first_key)
        remote.remove(first_key)

        self.assertFalse(remote.checkpresent(first_key))
        self.assertTrue(remote.checkpresent(second_key))
        with ZipFile(remote._get_zip_path(second_key)) as archive:
            self.assertEqual(archive.read(second_key), second_payload)

    def test_delete_matches_names_literally_and_preserves_other_members(self):
        zip_path = self.remote_directory / "archive with spaces.zip"
        with ZipFile(zip_path, "w") as archive:
            archive.comment = b"keep this comment"
            archive.writestr("same.a", b"a")
            archive.writestr("same.?", b"question mark")
            archive.writestr("same.b", b"b")

        delete_from_zip(zip_path, "same.?")

        with ZipFile(zip_path) as archive:
            self.assertEqual(archive.namelist(), ["same.a", "same.b"])
            self.assertEqual(archive.read("same.a"), b"a")
            self.assertEqual(archive.read("same.b"), b"b")
            self.assertEqual(archive.comment, b"keep this comment")

    def test_delete_is_a_no_op_for_a_missing_member(self):
        zip_path = self.remote_directory / "archive.zip"
        with ZipFile(zip_path, "w") as archive:
            archive.writestr("present", b"payload")
        original = zip_path.read_bytes()

        delete_from_zip(zip_path, "missing")

        self.assertEqual(zip_path.read_bytes(), original)

    def test_delete_translates_corrupt_archives_and_cleans_temporary_files(self):
        zip_path = self.remote_directory / "archive.zip"
        zip_path.write_bytes(b"corrupt")
        with self.assertRaisesRegex(RemoteError, "Could not delete"):
            delete_from_zip(zip_path, "member")

        with ZipFile(zip_path, "w") as archive:
            archive.writestr("member", b"payload")
        with patch.object(Path, "replace", side_effect=OSError("replace failed")):
            with self.assertRaisesRegex(RemoteError, "replace failed"):
                delete_from_zip(zip_path, "member")
        self.assertEqual(
            list(self.remote_directory.glob(".archive.zip.*.manyzips-temp")), []
        )

    def test_archive_lock_translates_open_and_lock_failures(self):
        zip_path = self.remote_directory / "archive.zip"
        with patch(
            "git_annex_remote_manyzips.manyzips.open",
            side_effect=OSError("open failed"),
        ):
            with self.assertRaisesRegex(RemoteError, "Could not open lock"):
                with archive_lock(zip_path, exclusive=True):
                    pass

        with patch(
            "git_annex_remote_manyzips.manyzips.flock",
            side_effect=OSError("lock failed"),
        ):
            with self.assertRaisesRegex(RemoteError, "Could not lock"):
                with archive_lock(zip_path, exclusive=False):
                    pass

    def test_address_falls_back_to_a_hash_for_unsafe_key_characters(self):
        remote, _ = self.make_remote(address_length="3")
        key = "WORM-s1--../unsafe"

        zip_path = remote._get_zip_path(key)

        self.assertEqual(zip_path.parent, self.remote_directory)
        self.assertNotIn("..", zip_path.name)

    def test_key_size_parser_handles_extensions_and_malformed_keys(self):
        self.assertEqual(ManyZips._get_size_from_key("SHA256E-s12--abc.txt"), 12)
        self.assertEqual(
            ManyZips._get_size_from_key("WORM-s12-m1--name--with-dashes"), 12
        )
        self.assertIsNone(ManyZips._get_size_from_key("GPGHMACSHA1--abc"))
        self.assertIsNone(ManyZips._get_size_from_key("SHA256E-sbad--abc"))
        self.assertIsNone(ManyZips._get_size_from_key("malformed"))

    def test_optional_protocol_answers(self):
        remote, _ = self.make_remote()
        key = self.key(b"x", prefix="ff")

        self.assertEqual(remote.getavailability(), "local")
        self.assertTrue(remote.whereis(key).endswith(f"ff.zip/{key}"))


class EntrypointTests(unittest.TestCase):
    def test_module_starts_the_external_remote_protocol(self):
        process = subprocess.run(
            [sys.executable, "-m", "git_annex_remote_manyzips.manyzips"],
            input="",
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )

        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertEqual(process.stdout, "VERSION 1\n")
        self.assertEqual(process.stderr, "")


if __name__ == "__main__":
    unittest.main()
