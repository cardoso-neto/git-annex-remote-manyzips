# git-annex-remote-manyzips
External remote that can be easily and quickly copied across volumes/partitions/disks.

This is very similar to a directory special remote, but files are stored inside `.zip` archives (compressed or not) to allow for faster copies to other drives by minimizing random access look-ups on disks.
Useful for repos with several thousand small files.
Especially useful if they're text files, because then you could use compression.

## Options overview

- `address_length` - use 1, 2, or 3 prefix characters to select a ZIP. For hexadecimal keys, this gives up to `16^address_length` ZIPs. e.g.: `address_length=2`
- `compression` - `store` for no compression, `lzma` for stronger compression, or `deflate` for faster compression. The default is `store`.
- `directory` - define in which folder data will be stored. e.g.: `directory=~/zipsannex/`

### Cryptography-related options

- `encryption` - One of "none", "hybrid", "shared", "pubkey" or "sharedpubkey".
See [encryption](https://git-annex.branchable.com/encryption/).

The following options are only relevant if `encryption` is not "none".

- `chunk` - This is the size in which git-annex splits the keys prior to uploading, see [chunking](https://git-annex.branchable.com/chunking).
- `keyid` - Choose the gpg key to use for encryption. e.g.: `keyid=2512E3C7` or `keyid=name@email.com`
- `mac` - The MAC algorithm used for the "key-hashing" the filenames. `HMACSHA256` is recommended.

## Install

You need [git](https://git-scm.com/book/en/v2/Getting-Started-Installing-Git) and [git-annex](https://git-annex.branchable.com/install/) already installed.

Install [uv](https://docs.astral.sh/uv/getting-started/installation/). The project requires Python 3.14; uv downloads and manages it automatically when needed.

### Install the remote

```sh
git clone https://github.com/cardoso-neto/git-annex-remote-manyzips.git
cd git-annex-remote-manyzips
uv tool install .
```

## Usage

```
git init
git annex init
git annex add $yourfiles

git annex initremote $remotename \
  type=external externaltype=manyzips encryption=none \
  address_length=2 compression=lzma directory=/mnt/drive/zipsannex

git annex copy --to $remotename
``` 

## Options (detailed)

#### `address_length`

This parameter controls how many characters of the beginning of a file's hex hash digest will be used for the `.zip` file path.
e.g.: if `address_length = 3` and `SHA256E-s50621986--ddd1a997afaf60c981fbfb1a1f3a600ff7bad7fccece9f2508fb695b8c2f153d` as the file to be stored, the `.zip` path will be `ddd.zip` and all files stored here would go into one of 4096 buckets.

These counts apply to hexadecimal keys, such as SHA256E. Other key types can use letters, digits, `_`, and `-`. They can produce more ZIPs and an uneven distribution. The remote hashes a prefix that contains unsupported characters. The address rule stays the same so existing archives remain accessible.

#### `compression`

Not recommended for use with encryption, because the data already flows through gzip before being ciphered.

`stored` and `deflated`, accepted by older documentation/code, remain supported as aliases for `store` and `deflate`.

#### `chunk`

This is the amount of disk space that will additionally be used during upload.
Usually useful to hide how large are your files.
Also, if you want to access a file while it's still being downloaded using [git-annex-inprogress](https://git-annex.branchable.com/git-annex-inprogress/).
If you use it, a value between 50MiB and 500MiB is probably a good idea.
Smaller values mean more disk seeks for presence check of big files which can slow down `fsck`, `drop` or `move`.
Bigger values mean more waiting time before being able to access the downloaded file via `git annex inprogress`.

#### `directory`

It is also possible (though not recommended unless you really know what you are doing) to use a single directory to store several repositories' data by pointing their manyzips remotes to the same folder path.
No problems will arise, but to avoid data loss you should not ever remove files from this remote, because if you remove a key that was present in another repo that repo will not be notified.

#### `mac`

Default is `HMACSHA1` and the strongest is `HMACSHA512`, which could end up resulting in too large a file-name.
Hence, `HMACSHA256` is the recommended one.
See [MAC algorithm](https://git-annex.branchable.com/encryption/#index5h2).

## Testing

Sync the development environment and run the complete suite:

```sh
uv sync
./test.sh
```

The suite requires `git` and `git-annex`. It runs Ruff and tests real repositories through `git-annex`. Tests include compression, damaged content, parallel transfers, reads during an active store, read-only storage, and failed writes. The write-failure test uses an OS file-size limit to cause a real I/O error. It does not fill the host disk.

## Data safety and failure behavior

**Presence checks never change stored content.** A missing member or a known size mismatch is reported as absent. An unreadable ZIP is reported as an error. Checks do not detect corruption that leaves the size unchanged; use a full `git annex fsck --from REMOTE` to check content.

Reads need no locks or write access to the archive directory. Writers still take a lock to prevent two writers from changing the same ZIP at once. During a store, a read of any member in that ZIP can fail because the ZIP index is incomplete. Presence checks retry an invalid index three times, with a 50 ms delay each time. If the index is still unreadable, the remote returns an error. Retrieval errors also ask the caller to retry after the store ends. Parallel uploads can need a retry too, because git-annex checks for content before it starts each upload.

Retrieval writes to a temporary file before replacing its destination. Deletion writes a temporary archive before replacing the original. A store appends to the selected ZIP for speed. Before it appends, the remote saves the old ZIP index in memory. If the append fails, it removes the new bytes and restores the index. This preserves the other members without copying their content. When replacing a key, the old member is removed first; a failed replacement can leave that key absent.

Recovery needs a working disk and a running process. If recovery writes also fail, the remote reports that failure. `SIGKILL`, a kernel crash, power loss, or device removal can still damage the ZIP. There is no power-loss durability guarantee. After a crash, keep a copy of the affected ZIP before repair and run `git annex fsck --from REMOTE`. A check can find damage; it cannot guarantee repair. Keep another verified copy of important content.

## Tips

### Making archives contiguous in disk

The ext4 filesystem already does a splendid job of that, so this is probably unnecessary, but you can always make sure of it by running `e4defrag` on your `directory`.

### `.zip` file counts

You don't want to let your `.zip`s get too big.
New stores and reads inspect the selected archive's index. That metadata work grows with the number of keys in the bucket. Apart from the key being transferred, they do not read or rewrite other members' content. The index saved for write recovery also uses memory in proportion to its size.

Deletion and replacement of an existing key rebuild the selected archive. The remote reads and recompresses the remaining members. This takes time and temporary disk space in proportion to the archive's contents. Deletions are expected to be rare. A larger `address_length` spreads keys among more archives and can reduce this cost. ZIP has no portable constant-time deletion that also recovers the deleted member's space.
