#!/usr/bin/env python
"""Store git-annex keys in a configurable set of ZIP archives."""

import sys
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from fcntl import LOCK_EX, LOCK_SH, flock
from functools import cached_property
from hashlib import sha256
from os.path import relpath as get_relative_path
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import BinaryIO
from zipfile import ZIP_DEFLATED, ZIP_LZMA, ZIP_STORED, BadZipFile, ZipFile, ZipInfo

from annexremote import Master, RemoteError, SpecialRemote

COMPRESSION_ALGORITHMS = {
    "store": ZIP_STORED,
    "lzma": ZIP_LZMA,
    "deflate": ZIP_DEFLATED,
}
COMPRESSION_ALIASES = {"stored": "store", "deflated": "deflate"}


def _mkdir(directory: Path):
    try:
        directory.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        raise RemoteError(
            f"Failed to write to {str(directory)!r}; {error!r}."
        ) from error


@contextmanager
def archive_lock(zip_path: Path, exclusive: bool) -> Iterator[None]:
    """Lock one archive across concurrently running remote processes."""
    lock_directory = zip_path.parent / ".locks"
    _mkdir(lock_directory)
    lock_path = lock_directory / f"{zip_path.name}.lock"
    try:
        lock_file = open(lock_path, "a+b")
    except OSError as error:
        raise RemoteError(
            f"Could not open lock for {zip_path.name!r}: {error}."
        ) from error
    with lock_file:
        try:
            flock(lock_file, LOCK_EX if exclusive else LOCK_SH)
        except OSError as error:
            raise RemoteError(f"Could not lock {zip_path.name!r}: {error}.") from error
        yield


def copyfileobj(
    fsrc: BinaryIO,
    fdst: BinaryIO,
    length: int = 1024 * 1024,  # 2 ** 20, 1 MiB
    callback: Callable[[int], None] = lambda x: None,
    file_size: int | None = None,
):
    """
    Copy data while passing the progress through a callback every length bytes.

    shutil.copyfileobj reimplementation with:
    - a bigger default buffer length;
    - a callback to track copying progress;
    - a file_size argument to avoid allocating an unnecessarily big buffer.
    Copy data from file-like obj fsrc to file-like obj fdst.
    """
    if file_size is not None:
        length = min(length, file_size)
    # Localize variable access to minimize overhead.
    fsrc_read = fsrc.read
    fdst_write = fdst.write
    streamed_bytes = 0
    while buf := fsrc_read(length):
        streamed_bytes += len(buf)
        fdst_write(buf)
        callback(streamed_bytes)


def delete_from_zip(zip_path: Path, file_to_delete: str):
    """Atomically rewrite an archive without members matching the exact name."""
    temporary_path = None
    try:
        archive_mode = zip_path.stat().st_mode & 0o7777
        with ZipFile(zip_path) as source:
            if file_to_delete not in source.namelist():
                return
            with NamedTemporaryFile(
                dir=zip_path.parent,
                prefix=f".{zip_path.name}.",
                suffix=".manyzips-temp",
                delete=False,
            ) as temporary_file:
                temporary_path = Path(temporary_file.name)
            with ZipFile(temporary_path, "w", allowZip64=True) as destination:
                destination.comment = source.comment
                for info in source.infolist():
                    if info.filename == file_to_delete:
                        continue
                    with (
                        source.open(info) as member,
                        destination.open(info, "w") as output,
                    ):
                        copyfileobj(member, output, file_size=info.file_size)
        temporary_path.chmod(archive_mode)
        temporary_path.replace(zip_path)
        temporary_path = None
    except (BadZipFile, OSError, RuntimeError) as error:
        raise RemoteError(
            f"Could not delete {file_to_delete!r} from {zip_path.name!r}: {error}."
        ) from error
    finally:
        if temporary_path is not None:
            try:
                temporary_path.unlink()
            except FileNotFoundError:
                pass


class ManyZips(SpecialRemote):
    """A local git-annex special remote backed by bucketed ZIP files."""

    def __init__(self, annex: Master):
        super().__init__(annex)
        self.annex = annex
        self.configs = {
            "address_length": "1 for 16 .zips, 2 for 256, and 3 for 4096.",
            "directory": "Folder to store data.",
            "compression": "'store' for none, 'lzma' for LZMA, or 'deflate' for DEFLATE.",
        }

    @cached_property
    def address_length(self) -> int:
        """Return the validated number of key characters used as an address."""
        value = self.annex.getconfig("address_length")
        try:
            address_length = int(value) if value else 1
        except ValueError as error:
            raise RemoteError(
                "address_length must be an integer from 1 to 3."
            ) from error
        if not 1 <= address_length <= 3:
            raise RemoteError("address_length must be an integer from 1 to 3.")
        return address_length

    @cached_property
    def compression(self) -> str:
        """Return the configured compression name in canonical form."""
        configured = self.annex.getconfig("compression")
        if not configured:
            configured = "store"
            self.annex.setconfig("compression", configured)
        compression = COMPRESSION_ALIASES.get(configured, configured)
        if compression not in COMPRESSION_ALGORITHMS:
            msg = f"Compression type {configured!r} is not available.\n"
            msg += "Use 'store', 'lzma', or 'deflate'."
            raise RemoteError(msg)
        if compression != configured:
            self.annex.setconfig("compression", compression)
        return compression

    @cached_property
    def directory(self) -> Path:
        """Return the configured storage directory as an absolute path."""
        directory = self.annex.getconfig("directory")
        if not directory:
            raise RemoteError("You need to set directory=")
        directory = Path(directory).expanduser().resolve()
        return directory

    def initremote(self):
        """Validate configuration and create the storage directory."""
        self.info = {
            "address_length": self.address_length,
            "compression": self.compression,
            "directory": self.directory,
        }
        _mkdir(self.directory)

    def prepare(self):
        """Verify that the storage directory is available."""
        if not self.directory.is_dir():
            raise RemoteError(f"{str(self.directory)!r} not found.")

    def transfer_store(self, key: str, local_file: str):
        """Store a local git-annex object under its key."""
        file_path = Path(local_file)
        zip_path = self._get_zip_path(key)
        try:
            file_size = file_path.stat().st_size
            compression_algorithm = COMPRESSION_ALGORITHMS[self.compression]
            with archive_lock(zip_path, exclusive=True):
                if self._member_exists_unlocked(key):
                    delete_from_zip(zip_path, key)

                zinfo = ZipInfo.from_file(
                    file_path, arcname=key, strict_timestamps=False
                )
                zinfo.compress_type = compression_algorithm
                try:
                    with ZipFile(
                        zip_path,
                        "a",
                        compression=compression_algorithm,
                        allowZip64=True,
                    ) as myzip:
                        with (
                            open(file_path, "rb") as src,
                            myzip.open(zinfo, "w") as dest,
                        ):
                            copyfileobj(
                                src,
                                dest,
                                callback=self.annex.progress,
                                file_size=file_size,
                            )
                except BaseException as transfer_error:
                    try:
                        if self._member_exists_unlocked(key):
                            delete_from_zip(zip_path, key)
                    except RemoteError as cleanup_error:
                        raise RemoteError(
                            f"The transfer failed and its partial key could not be removed: "
                            f"{cleanup_error}"
                        ) from transfer_error
                    raise
                if not self._check_file_sizes_unlocked(key, file_path):
                    if self._member_exists_unlocked(key):
                        delete_from_zip(zip_path, key)
                    raise RemoteError("The stored key did not match the source size.")
        except (BadZipFile, OSError, RuntimeError, ValueError) as error:
            raise RemoteError(f"Could not store {key!r}: {error}.") from error

    def transfer_retrieve(self, key: str, local_file: str):
        """Retrieve a key without exposing a partial destination file."""
        file_path = Path(local_file)
        zip_path = self._get_zip_path(key)
        tempfile_path = None
        try:
            with archive_lock(zip_path, exclusive=False):
                with ZipFile(zip_path) as myzip:
                    zinfo = myzip.getinfo(key)
                    with (
                        myzip.open(zinfo) as myfile,
                        NamedTemporaryFile(
                            mode="wb",
                            dir=file_path.parent,
                            prefix=f".{file_path.name}.",
                            suffix=".manyzips-temp",
                            delete=False,
                        ) as f_out,
                    ):
                        tempfile_path = Path(f_out.name)
                        copyfileobj(
                            myfile,
                            f_out,
                            callback=self.annex.progress,
                            file_size=zinfo.file_size,
                        )
                tempfile_path.replace(file_path)
                tempfile_path = None
        except (BadZipFile, KeyError, OSError, RuntimeError) as error:
            raise RemoteError(f"Could not retrieve {key!r}: {error}.") from error
        finally:
            if tempfile_path is not None:
                try:
                    tempfile_path.unlink()
                except FileNotFoundError:
                    pass

    def checkpresent(self, key: str) -> bool:
        """Return whether a complete key is present in its archive."""
        zip_path = self._get_zip_path(key)
        if not zip_path.is_file():
            return False
        try:
            with archive_lock(zip_path, exclusive=False):
                return self._checkpresent_unlocked(key)
        except (BadZipFile, OSError, RuntimeError) as error:
            raise RemoteError(f"Could not check {key!r}: {error}.") from error

    def remove(self, key: str):
        """Remove a key while retaining all other members in its archive."""
        zip_path = self._get_zip_path(key)
        if not zip_path.is_file():
            return
        try:
            with archive_lock(zip_path, exclusive=True):
                if not self._member_exists_unlocked(key):
                    return
                delete_from_zip(zip_path, key)
                if self._member_exists_unlocked(key):
                    raise RemoteError(f"Could not remove {key!r}.")
        except (BadZipFile, OSError, RuntimeError) as error:
            raise RemoteError(f"Could not remove {key!r}: {error}.") from error

    def _member_exists_unlocked(self, key: str) -> bool:
        zip_path = self._get_zip_path(key)
        if not zip_path.is_file():
            return False
        with ZipFile(zip_path) as myzip:
            try:
                myzip.getinfo(key)
            except KeyError:
                return False
            return True

    def _check_file_sizes_unlocked(self, key: str, file_path: Path) -> bool:
        zip_path = self._get_zip_path(key)
        if not zip_path.is_file():
            return False
        with ZipFile(zip_path) as myzip:
            try:
                zinfo = myzip.getinfo(key)
            except KeyError:
                return False
            return zinfo.file_size == file_path.stat().st_size

    def _checkpresent_unlocked(self, key: str) -> bool:
        zip_path = self._get_zip_path(key)
        if not zip_path.is_file():
            return False
        with ZipFile(zip_path) as myzip:
            try:
                zinfo = myzip.getinfo(key)
            except KeyError:
                return False
        key_size = self._get_size_from_key(key)
        return key_size is None or key_size == zinfo.file_size

    def _get_address(self, key: str) -> str:
        # "SHA256E-s148273064--5880ac1cd05eee9...eef465ebd3.wav"
        parts = key.rsplit("--", 1)
        # ["SHA256E-s148273064", "5880ac1cd05eee9...eef465ebd3.wav"]
        address = parts[-1][: self.address_length]
        if not address or any(
            character
            not in "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ_-"
            for character in address
        ):
            address = sha256(key.encode("utf-8")).hexdigest()[: self.address_length]
        # "588"
        return address

    def _get_zip_path(self, key: str) -> Path:
        zip_path_and_stem = self.directory / self._get_address(key)
        return zip_path_and_stem.with_suffix(".zip")

    @staticmethod
    def _get_size_from_key(key: str) -> int | None:
        metadata = key.split("--", 1)[0]
        # "GPGHMACSHA1", "d0a3fc75bb721eb4ffbf84f13ffc4e4583c25c76"
        # "SHA256E-s148273064", "5880ac1cd05eee9...eef465ebd3.wav"
        parts = metadata.split("-s", 1)
        # ["GPGHMACSHA1"]
        if len(parts) > 1:
            # ["SHA256E", "148273064"]
            size = parts[1].split("-", 1)[0]
            if size.isdigit():
                return int(size)
        return None

    def getavailability(self) -> str:
        """Tell git-annex that this remote is available only on this system."""
        return "local"

    def whereis(self, key: str) -> str:
        """
        Return the path of a file inside the ZIP where it is stored.

        `unzip -p path/to/archive.zip key.ext > file.ext` can be used to extract it.
        `fuse-zip path/to/archive.zip` as well.
        https://unix.stackexchange.com/q/14120 for more.
        """
        key_path = self._get_zip_path(key) / key
        return get_relative_path(key_path)


def main():
    """Run the annexremote protocol loop without writing chatter to stdout."""
    output = sys.stdout
    sys.stdout = sys.stderr

    master = Master(output)
    remote = ManyZips(master)
    master.LinkRemote(remote)
    master.Listen()


if __name__ == "__main__":
    main()
