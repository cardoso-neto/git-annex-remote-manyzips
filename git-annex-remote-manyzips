#!/usr/bin/env python

import inspect
import os
import zipfile
import sys
from datetime import datetime
from functools import lru_cache
from os.path import relpath as get_relative_path
from pathlib import Path
from subprocess import CompletedProcess, run
from shlex import split
from typing import BinaryIO, Callable, List, Optional
from zipfile import ZIP_DEFLATED, ZIP_LZMA, ZIP_STORED
from zipfile import Path as ZipPath, ZipFile, ZipInfo

from annexremote import Master, ProtocolError, RemoteError, SpecialRemote
from overrides import overrides


LOGFOLDER = Path("./logs/")


class UnsupportedCompression(RemoteError):
    pass


def lazy_property(wrapped: Callable) -> Callable:
    """
    Shortcut for the property-lru_cache double-decoration.

    i.e.:
    @property
    @functools.lru_cache()
    def foo(self): ...
    https://stackoverflow.com/a/19979379/11615853
    """
    return property(lru_cache()(wrapped))


def log_stuff(log_path: Path, lines: List[str]):
    log_path.parent.mkdir(exist_ok=True)
    with open(log_path, "a") as stdout_file:
        stdout_file.write(f"{datetime.now()}\n")
        stdout_file.write(f"{inspect.stack()[2].function}->")
        stdout_file.write(f"{inspect.stack()[1].function}\n")
        stdout_file.write("\n".join(lines))


def _mkdir(directory: Path):
    try:
        directory.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        raise RemoteError(f"Failed to write to {str(directory)!r}")


def copyfileobj(
    fsrc: BinaryIO,
    fdst: BinaryIO,
    length: int = 1024 * 1024,  # 2 ** 20, 1 MiB
    callback: Callable[[int], None] = lambda x: None,
    file_size: int = None,
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


def delete_from_zip(zip_path: Path, file_to_delete: Path):
    args = split(f"zip --delete {zip_path} {file_to_delete}")
    proc: CompletedProcess = run(args, capture_output=True, text=True)
    log_stuff(
        zip_path.parent / LOGFOLDER / f"{zip_path.stem}.log", [proc.stdout, proc.stderr]
    )
    if proc.returncode != 0:
        raise RemoteError(
            f"Could not delete {file_to_delete!r} from {zip_path.name!r}."
        )


class ManyZips(SpecialRemote):

    def __init__(self, annex: Master):
        super().__init__(annex)
        self.annex = annex
        self.configs = {
            "address_length": "1 for 16 .zips, 2 for 256, and 3 for 4096.",
            "directory": "Folder to store data.",
            "compression": "'store' for no compression, 'lzma' for .xz, and 'deflate' for .zip."
        }
        self.compression_algos = {
            "store": ZIP_STORED,
            "lzma": ZIP_LZMA,
            "deflated": ZIP_DEFLATED,
        }

    @lazy_property
    def address_length(self) -> int:
        address_length = self.annex.getconfig("address_length")
        address_length = int(address_length) if address_length != "" else 1
        if not 0 < address_length < 3:
            msg = "address_length value should be > 0 and < 3."
            raise RemoteError(msg)
        return address_length

    @lazy_property
    def compression(self) -> int:
        _compression = self.annex.getconfig("compression")
        if not _compression:
            _compression = "store"
            self.annex.setconfig("compression", _compression)
        elif _compression not in self.compression_algos:
            msg = f"Compression type {_compression!r} is not available.\n"
            msg += "Use 'store', 'lzma', or 'deflated'."
            raise UnsupportedCompression(msg)
        return _compression

    @lazy_property
    def directory(self) -> Path:
        directory = self.annex.getconfig("directory")
        if not directory:
            raise RemoteError("You need to set directory=")
        directory = Path(directory).resolve()
        return directory

    @overrides
    def initremote(self):
        self.info = {
            "address_length": self.address_length,
            "compression": self.compression,
            "directory": self.directory,
        }
        _mkdir(self.directory)

    @overrides
    def prepare(self):
        if not self.directory.is_dir():
            raise RemoteError(f"{str(self.directory)!r} not found.")
        self.compression_algorithm = self.compression_algos[self.compression]

    @overrides
    def transfer_store(self, key: str, filename: str):
        """

        e.g.:
        filename=".git/annex/objects/qW/pV/SHA256E-s148273064--5880ac1cd05eee90db251c027771b4c9f0a55b7c8b2813c95eff59eef465ebd3.wav/SHA256E-s148273064--5880ac1cd05eee90db251c027771b4c9f0a55b7c8b2813c95eff59eef465ebd3.wav"
        """
        file_path = Path(filename)
        if self.check_file_sizes(key, file_path):
            return
        zip_path = self._get_zip_path(key)
        zinfo = ZipInfo.from_file(
            file_path, arcname=key, strict_timestamps=True
        )
        zinfo.compress_type = self.compression_algorithm
        # TODO: create inconsistent state context manager to avoid partial/corrupt
        # transfers when user KeyboardInterrupts during a copyfileobj call
        with ZipFile(zip_path, 'a', compression=self.compression_algorithm, allowZip64=True) as myzip:
            with open(file_path, "rb") as src, myzip.open(zinfo, 'w') as dest:
                copyfileobj(src, dest, callback=self.annex.progress, file_size=file_path.stat().st_size)
        if not self.check_file_sizes(key, file_path):
            print("Unknown error while storing the key.")
            print("Attempting to delete corrupt key from remote...")
            delete_from_zip(zip_path, key)
            print("Key successfully deleted.")
            msg = "Could not store this key. drop it --from this-remote and retry."
            raise RemoteError(msg)

    @overrides
    def transfer_retrieve(self, key: str, filename: str):
        zip_path = self._get_zip_path(key)
        with ZipFile(zip_path) as myzip:
            zinfo = myzip.getinfo(key)
            with myzip.open(zinfo) as myfile:
                # TODO: copy to a tempfile then rename
                with open(filename, 'wb') as f_out:
                    copyfileobj(myfile, f_out, callback=self.annex.progress, file_size=zinfo.compress_size)

    @overrides
    def checkpresent(self, key: str) -> bool:
        zip_path = self._get_zip_path(key)
        if zip_path.is_file():
            with ZipFile(zip_path) as myzip:
                if key in myzip.namelist():
                    zinfo = myzip.getinfo(key)
                    # TODO: check for size mismatch
                    # encrypted files don't have their size listed
                    # self.annex.debug(f'key: "{key.split("-")}"')
                    # self.annex.debug(f"zipped: '{zinfo.file_size}'")
                    return True
        return False

    def check_file_sizes(self, key: str, file_path: Path) -> bool:
        zip_path = self._get_zip_path(key)
        if zip_path.is_file():
            with ZipFile(zip_path) as myzip:
                if key in myzip.namelist():
                    zinfo = myzip.getinfo(key)
                    if zinfo.file_size == file_path.stat().st_size:
                        return True
        return False

    @overrides
    def remove(self, key: str):
        if not self.checkpresent(key):
            return
        zip_path = self._get_zip_path(key)
        delete_from_zip(zip_path, key)
        if self.checkpresent(key):
            raise RemoteError("Could not remove.")

    def _get_address(self, key: str) -> str:
        # TODO: use self.annex.dirhash_lower(key).replace("/", "")[:self.address_length]
        self.annex.dirhash_lower
        # "SHA256E-s148273064--5880ac1cd05eee9...eef465ebd3.wav"
        parts = key.split("-")
        # ["SHA256E", "s148273064", "5880ac1cd05eee9...eef465ebd3.wav"
        address = parts[-1][:self.address_length]
        # "588"
        return address

    def _get_zip_path(self, key: str) -> Path:
        zip_path_and_stem = self.directory / self._get_address(key)
        return zip_path_and_stem.with_suffix(".zip")

    @overrides
    def getavailability(self) -> str:
        return "local"

    @overrides
    def whereis(self, key: str) -> str:
        """
        Return the path of a file inside the .zip its stored.

        `unzip -p path/to/archive.zip key.ext > file.ext` can be used to extract it.
        `fuse-zip path/to/archive.zip` as well.
        https://unix.stackexchange.com/q/14120 for more.
        """
        key_path = self._get_zip_path(key) / key
        return get_relative_path(key_path)


def main():
    output = sys.stdout
    sys.stdout = sys.stderr

    master = Master(output)
    remote = ManyZips(master)
    master.LinkRemote(remote)
    master.Listen()


if __name__ == "__main__":
    main()
