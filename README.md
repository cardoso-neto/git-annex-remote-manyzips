# git-annex-remote-manytars
External remote that can be easily and quickly copied.

This is very similar to a directory special remote, but files are stored inside `.zip` archives (compressed or not) to allow for faster copies to other drives by minimizing random access look-ups on disks.

## Options overview

- `address_length` - control into how many `.zip`s your files will be split: `number_of_zips = 16^address_length`.
- `compression` - Either `stored` for no compression, `lzma` for .7z/.xz compression, and `deflate` for the good ol' light-on-CPU .zip level 8 compression. Not recommended for use with encryption, because they data will flow through gzip before being ciphered.
- `directory` - define in which folder data will be stored.

### Cryptography-related options

- `encryption` - One of "none", "hybrid", "shared", "pubkey" or "sharedpubkey". See [encryption](https://git-annex.branchable.com/encryption/).

The following options are only relevant if `encryption` is not "none".

- `keyid` - Choose the gpg key to use for encryption.
- `mac` - The MAC algorithm used for the "key-hashing" the filenames. `HMACSHA256` is recommended.
- `chunk` - This is the size in which git-annex splits the keys prior to uploading, see [chunking](https://git-annex.branchable.com/chunking).
This is the amount of disk space that will additionally be used during upload.
Usually useful to hide how large are your files.
Also, if you want to access a file while it's still being downloaded using [git-annex-inprogress](https://git-annex.branchable.com/git-annex-inprogress/).
If you use it, a value between 50MiB and 500MiB is probably a good idea.
Smaller values mean more API calls for presence check of big files which can slow down fsck, drop or move.
Bigger values mean more waiting time before being able to access the downloaded file via `git annex inprogress`.
<!--
## Things you should be aware of

### Making archives contiguous in disk

The ext4 filesystem already does a splendid job of that, but you can always make sure of it by running `e4defrag` on your `directory`.

### `.tar` sizes

You don't want to let your `.tar`s get too big, otherwise insertions will start taking longer and longer.
Each insertion rebuilds the whole index for that archive, so it's an `O(n/number_of_buckets)` operation.

## Options (detailed)

#### `directory`

It is also possible (though not recommended unless you really know what you are doing) to use a single directory to store several repositories' data by poiting their manytars remotes to the same folder path.
No problems will arise, but to avoid data loss you should not ever remove files from this remote, because if you remove a key that was present in another repo that repo will not be notified.

#### `address_length`

This parameter ontrols how many characters of the beginning of a file's hex hash digest will be used for the `.tar` file path.
e.g.: if `address_length = 3` and `SHA256E-s50621986--ddd1a997afaf60c981fbfb1a1f3a600ff7bad7fccece9f2508fb695b8c2f153d` as the file to be stored, the `.tar` path will be `ddd.tar` and all files stored here would go into one of 4096 buckets.
You don't want to let your `.tar`s get too big, otherwise insertions will start taking longer and longer, because each insertion requires me to rebuild the whole index for that archive, so it's `O(n/number_of_buckets)` operation.
-->
