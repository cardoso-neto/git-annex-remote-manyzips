# git-annex-remote-manyzips
External remote that can be easily and quickly copied across volumes/partitions/disks.

This is very similar to a directory special remote, but files are stored inside `.zip` archives (compressed or not) to allow for faster copies to other drives by minimizing random access look-ups on disks.
Useful for repos with several thousand small files.
Especially useful if they're text files, because then you could use compression.

## Options overview

- `address_length` - control into how many `.zip`s your files will be split: `number_of_zips = 16^address_length`. e.g.: `address_length=2`
- `compression` - Either `stored` for no compression, `lzma` for `.7z`/`.xz` compression, and `deflate` for the good ol' light-on-CPU `.zip` level 8 compression.
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

You also need `python>=3.8` and `pip`.
I personally recommend [miniconda](https://conda.io/miniconda.html) to install those.
It's like [nvm](https://github.com/nvm-sh/nvm), but for python.

### `pip` (recommended)

```
git clone https://github.com/cardoso-neto/git-annex-remote-manyzips.git
cd git-annex-remote-manyzips
pip install -e ./
```

### Manual

Clone this repo and symlink/hardlink/copy [git_annex_remote_manyzips/manyzips](git_annex_remote_manyzips/manyzips) to somewhere in `$PATH`.

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

This parameter ontrols how many characters of the beginning of a file's hex hash digest will be used for the `.zip` file path.
e.g.: if `address_length = 3` and `SHA256E-s50621986--ddd1a997afaf60c981fbfb1a1f3a600ff7bad7fccece9f2508fb695b8c2f153d` as the file to be stored, the `.zip` path will be `ddd.zip` and all files stored here would go into one of 4096 buckets.

#### `compression`

Not recommended for use with encryption, because the data already flows through gzip before being ciphered.

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

## Tips

### Making archives contiguous in disk

The ext4 filesystem already does a splendid job of that, so this is probably unnecessary, but you can always make sure of it by running `e4defrag` on your `directory`.

### `.zip` file counts

You don't want to let your `.zip`s get too big.
I'm pretty confident there is no operation that's `O(n)` on the size of the archives, but checking a `.zip`'s index is definitely `O(n)` on the number of files (`O(n/number_of_buckets)`) inside it.
That's what `address_length` is for.
