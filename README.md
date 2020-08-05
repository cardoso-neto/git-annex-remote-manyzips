# git-annex-remote-manytars
External remote that can be easily and quickly copied.

This is very similar to a directory special remote, but files are stored inside `.zip` archives (compressed or not) to allow for faster copies to other drives by minimizing random access look-ups on disks.
<!--
## Options overview

- `directory` - folder path to store the data.
- `address_length` - defines into how many `.tar`s you want to split your files, as in `number_of_tars = 16^address_length`. TODO: `address_length=0` stores everything in a single archive.

## Things you should be aware of

### Deleting files from this remote is slow

This is what I use to remove a file from an archive: [gnu.org/tar/delete](https://www.gnu.org/software/tar/manual/html_node/delete.html)

From what I can gather, it's an IO-bound `O(n)` operation on the size of the whole archive.
Can't be sure if it's a write-heavy one without some thorough testing, but I don't think that's the case.
It definitely is read-heavy, as it has to scan the whole archive looking for the file to be deleted. 

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
