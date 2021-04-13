# simple-filesystem
a FUSE filesystem driver that will let you mount a 1MB disk image (data file) as a filesystem inspired by the EXT linux filesystems

- Create Files
- List the files in the filesystem root directory (where you mounted it).
- Write to small files (under 4k).
- Read from small files (under 4k).
- Delete files.
- Rename files.


# advanced-filesystem
a FUSE filesystem driver as specified above plus:

- Read and write from files larger than one block. For example, you should be able to support one hundred 1k files or five 100k files.
- Create directories and nested directories. Directory depth should only be limited by disk space (and possibly the POSIX API).
- Remove directories.
- Hard links.
- Symlinks
- Support metadata (permissions and timestamps) for files and directories.
