// based on cs3650 starter code

#define _GNU_SOURCE
#include <assert.h>
#include <bsd/string.h>
#include <dirent.h>
#include <errno.h>
#include <fcntl.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <sys/mman.h>
#include <sys/stat.h>
#include <sys/sysmacros.h>
#include <sys/types.h>
#include <unistd.h>

#define FUSE_USE_VERSION 26
#include <fuse.h>

#include "bitmap.h"
#include "directory.h"
#include "inode.h"
#include "pages.h"
#include "slist.h"
#include "util.h"

int ROOT_PNUM = -1;

// implementation for: man 2 stat
// gets an object's attributes (type, permissions, size, etc)
int nufs_getattr(const char *path, struct stat *st) {
  int rv = 0;
  printf("entered gettattr with path %s\n", path);
  memset(st, 0, sizeof(stat));
  int parent_inum = tree_lookup(path);
  if (parent_inum < 0) {
    printf("getattr exited: failure, parent inum");
    return parent_inum;
  }

  inode *parent_inode = get_inode(parent_inum);
  char *filename = get_filename_from_path(path);
  printf("looking for filename: %s in directory with inode: %ld\n", filename,
         parent_inum);

  int desired_inum = directory_lookup(parent_inode, filename);

  if (desired_inum < 0) {
    // nufs_mknod(path,0100644,0);
    // return nufs_getattr(path,st);
    return -ENOENT;
  }

  inode *desired_inode = get_inode(desired_inum);
  st->st_mode = desired_inode->mode;
  st->st_size = desired_inode->size;
  st->st_uid = getuid();
  st->st_nlink = desired_inode->refs;
  st->st_ino = desired_inum;
  st->st_atime = desired_inode->accessed;
  st->st_mtime = desired_inode->modified;
  st->st_ctime = desired_inode->changed;
  printf("inum in gettattr: %ld\n", desired_inum);

  printf("getattr(%s) -> (%d) {mode: %04o, size: %ld}\n", path, rv, st->st_mode,
         st->st_size);
  return rv;
}

// implementation for: man 2 access
// Checks if a file exists.
int nufs_access(const char *path, int mask) {
  int rv = 0;
  struct stat st;
  rv = nufs_getattr(path, &st);
  printf("access(%s, %04o) -> %d\n", path, mask, rv);
  return rv;
}

// implementation for: man 2 readdir
// lists the contents of a directory
int nufs_readdir(const char *path, void *buf, fuse_fill_dir_t filler,
                 off_t offset, struct fuse_file_info *fi) {
  struct stat st;
  int rv = 0;

  nufs_getattr(path, &st);
  if (((&st)->st_mode & S_IXUSR) != S_IXUSR) {
    return -EACCES;
  }
  // will return contents of leaf directory as linkedlist, just their names

  slist *contents = directory_list(path);
  while (contents) {
    filler(buf, contents->data, &st, 0);
    contents = contents->next;
  }

  printf("readdir(%s) exited -> %d\n", path, rv);
  return rv;
}

// mknod makes a filesystem object like a file or directory
// called for: man 2 open, man 2 link
int nufs_mknod(const char *path, mode_t mode, dev_t rdev) {
  int rv = 0;

  int parent_inum = tree_lookup(path);
  if (parent_inum < 0) {
    printf("exiting mknod: failure: parent inum\n");
    return parent_inum;
  }
  inode *parent_inode = get_inode(parent_inum);
  char *filename = get_filename_from_path(path);

  int new_inum = alloc_inode();
  if (new_inum < 0) {
    printf("exiting mknod: failure: alloc inum\n");
    return -ENOSPC;
  }

  rv = directory_put(parent_inode, filename, new_inum);

  if (rv < 0) {
    printf("exiting mknod: failure: directory put\n");
    return -ENOSPC;
  }

  inode *new_inode = get_inode(new_inum);

  new_inode->mode = mode;
  new_inode->refs = 1;
  new_inode->size = 0;
  new_inode->accessed = time(NULL);
  new_inode->modified = time(NULL);
  new_inode->changed = time(NULL);

  printf("mknod(%s, %04o) -> %d\n", path, mode, rv);
  return rv;
}

int nufs_chmod(const char *path, mode_t mode) {
  int rv = 0;
  int parent_inum = tree_lookup(path);
  if (parent_inum < 0) {
    return parent_inum;
  }
  inode *parent_inode = get_inode(parent_inum);
  char *filename = get_filename_from_path(path);
  int desired_inum = directory_lookup(parent_inode, filename);
  if (desired_inum < 0) {
    return desired_inum;
  }
  inode *desired_inode = get_inode(desired_inum);
  desired_inode->mode = mode;
  desired_inode->changed = time(NULL);

  printf("chmod(%s, %04o) -> %d\n", path, mode, rv);
  return rv;
}

// most of the following callbacks implement
// another system call; see section 2 of the manual
int nufs_mkdir(const char *path, mode_t mode) {
  int rv = nufs_mknod(path, mode | 040000, 0);

  if (rv < 0) {
    return rv;
  }
  int parent_inum = tree_lookup(path);
  inode *parent_inode = get_inode(parent_inum);
  int desired_inum =
      directory_lookup(parent_inode, get_filename_from_path(path));

  ((direntry *)pages_get_page(get_inode(desired_inum)->ptrs[0]))[0].inum =
      desired_inum;
  strcpy(((direntry *)pages_get_page(get_inode(desired_inum)->ptrs[0]))[0].name,
         ".\0");

  ((direntry *)pages_get_page(get_inode(desired_inum)->ptrs[0]))[1].inum =
      parent_inum;
  strcpy(((direntry *)pages_get_page(get_inode(desired_inum)->ptrs[0]))[1].name,
         "..\0");

  assert(get_inode(desired_inum)->refs == 1);
  printf("mkdir(%s) -> %d\n", path, rv);
  return rv;
}

int nufs_unlink(const char *path) {
  int rv = 0;

  int parent_inum = tree_lookup(path);
  if (parent_inum < 0) {
    printf("exiting unlink: failure, tree_lookup\n");
    return parent_inum;
  }

  inode *parent_inode = get_inode(parent_inum);
  char *filename = get_filename_from_path(path);
  int desired_inum = directory_lookup(parent_inode, filename);
  if (desired_inum < 0) {
    printf("exiting unlink: failure, directory lookup\n");
    return desired_inum;
  }

  rv = directory_delete(parent_inode, filename);

  printf("unlink(%s) -> %d\n", path, rv);
  return rv;
}

int nufs_link(const char *from, const char *to) {
  int rv = -1;
  void *ibm = get_inode_bitmap();

  struct stat to_stat;
  struct stat from_stat;

  rv = nufs_getattr(to, &to_stat);
  if (rv >= 0) {
    return -EEXIST;
  }
  rv = nufs_getattr(from, &from_stat);
  if (rv < 0) {
    return -ENOENT;
  }

  int parent_inum_from = tree_lookup(from);
  if (parent_inum_from < 0) {
    return parent_inum_from;
  }
  inode *parent_inode_from = get_inode(parent_inum_from);
  char *filename_from = get_filename_from_path(from);

  int linking_inum = directory_lookup(parent_inode_from, filename_from);
  inode *linking_inode = get_inode(linking_inum);

  int parent_inum_to = tree_lookup(to);
  if (parent_inum_to < 0) {
    return parent_inum_to;
  }
  inode *parent_inode_to = get_inode(parent_inum_to);

  char *filename_to = get_filename_from_path(to);

  rv = directory_put(parent_inode_to, filename_to, linking_inum);
  if (rv < 0) {
    return -ENOSPC;
  }

  linking_inode->refs += 1;

  printf("link(%s => %s) -> %d\n", from, to, rv);
  return rv;
}

int nufs_rmdir(const char *path) {
  int rv = nufs_unlink(path);

  printf("rmdir(%s) -> %d\n", path, rv);
  return rv;
}

// implements: man 2 rename
// called to move a file within the same filesystem
int nufs_rename(const char *from, const char *to) {
  int rv = 0;
  nufs_link(from, to);
  nufs_unlink(from);
  printf("rename(%s => %s) -> %d\n", from, to, rv);
  return rv;
}

int nufs_truncate(const char *path, off_t size) {
  int rv = 0;
  printf("truncate(%s, %ld bytes) -> %d\n", path, size, rv);
  return rv;
}

// this is called on open, but doesn't need to do much
// since FUSE doesn't assume you maintain state for
// open files.
int nufs_open(const char *path, struct fuse_file_info *fi) {
  int rv = 0;
  printf("open(%s) -> %d\n", path, rv);
  return rv;
}

// Actually read data
int nufs_read(const char *path, char *buf, size_t size, off_t offset,
              struct fuse_file_info *fi) {
  struct stat st;
  int rv = 0;

  nufs_getattr(path, &st);
  if (((&st)->st_mode & S_IRUSR) != S_IRUSR) {
    return -EACCES;
  }

  int parent_inum = tree_lookup(path);

  if (parent_inum < 0) {
    printf("read eror: p");
    return parent_inum;
  }

  inode *parent_inode = get_inode(parent_inum);
  char *filename = get_filename_from_path(path);
  int desired_inum = directory_lookup(parent_inode, filename);
  if (desired_inum < 0) {
    printf("read eror: i");

    return desired_inum;
  }

  inode *desired_inode = get_inode(desired_inum);

  int bytes_read = 0;

  int pages_start = offset / 4096;                // inclusive floor
  int pages_end = bytes_to_pages(offset + size);  // exclusive ceiling

  void *desired_data_block = NULL;
  for (int ii = pages_start; ii < pages_end; ii++) {
    if (ii <= 1) {
      assert(desired_inode->ptrs[ii] != 0);
      desired_data_block = pages_get_page(desired_inode->ptrs[ii]);
    } else {
      int jj = ii - 2;
      int *iptr_arr = (int *)pages_get_page(desired_inode->iptr);
      assert(iptr_arr[jj] != 0);
      desired_data_block = pages_get_page(iptr_arr[jj]);
    }
    memcpy(buf + bytes_read, desired_data_block, min(size, 4096));
    bytes_read += min(size, 4096);
  }
  // desired_inode->size += bytes_written;
  assert(bytes_read == size);
  rv = bytes_read;

  desired_inode->accessed = time(NULL);

  printf("read(%s, %ld bytes, @+%ld) -> %d\n", path, size, offset, rv);
  return rv;
}

// Actually write data
int nufs_write(const char *path, const char *buf, size_t size, off_t offset,
               struct fuse_file_info *fi) {
  struct stat st;
  int rv = 0;

  nufs_getattr(path, &st);
  if (((&st)->st_mode & S_IWUSR) != S_IWUSR) {
    return -EACCES;
  }

  int parent_inum = tree_lookup(path);
  if (parent_inum < 0) {
    return parent_inum;
  }
  inode *parent_inode = get_inode(parent_inum);
  char *filename = get_filename_from_path(path);
  int desired_inum = directory_lookup(parent_inode, filename);
  if (desired_inum < 0) {
    printf("write failed: no file found\n");
    return desired_inum;
  }
  inode *desired_inode = get_inode(desired_inum);

  if (offset > desired_inode->size) {
    grow_inode(desired_inode, offset - desired_inode->size);
  }

  if (offset < desired_inode->size) {
    shrink_inode(desired_inode, offset);
  }

  int bytes_written = 0;

  int pages_start = offset / 4096;
  int pages_end = bytes_to_pages(offset + size);  // ending index, exclusive

  if (pages_start == pages_end) {
    pages_start--;
  }
  assert(pages_start >= 0);

  void *desired_data_block = NULL;
  for (int ii = pages_start; ii < pages_end; ii++) {
    if (ii <= 1) {
      if (desired_inode->ptrs[ii] == 0) {
        grow_inode(desired_inode, size);
      }
      assert(desired_inode->ptrs[ii] != 0);

      desired_data_block = pages_get_page(desired_inode->ptrs[ii]);
      if (ii == pages_start) {
        desired_data_block += (offset % PAGE_SIZE);
      }
    } else {
      if (desired_inode->iptr == 0) {
        grow_inode(desired_inode, size);
      }
      int iptr_index = ii - 2;
      assert(desired_inode->iptr != 0);
      int *iptr_arr = (int *)pages_get_page(desired_inode->iptr);
      if (iptr_arr[iptr_index] == 0) {
        grow_inode(desired_inode, size);
      }
      assert(iptr_arr[iptr_index] != 0);
      desired_data_block = pages_get_page(iptr_arr[iptr_index]);
    }
    assert(desired_data_block != NULL);
    memcpy(desired_data_block, buf + bytes_written, min(size, 4096));
    bytes_written += min(size, 4096);
    if (bytes_written % 4096 != 0) {
      assert(ii == pages_end - 1);
    }
  }
  desired_inode->size += bytes_written;
  assert(bytes_written == size);
  rv = bytes_written;

  desired_inode->modified = time(NULL);
  desired_inode->changed = time(NULL);

  printf("write(%s, %ld bytes, @+%ld) -> %d\n", path, size, offset, rv);
  return rv;
}

// Update the timestamps on a file or directory.
int nufs_utimens(const char *path, const struct timespec ts[2]) {
  int rv = -1;
  printf("utimens(%s, [%ld, %ld; %ld %ld]) -> %d\n", path, ts[0].tv_sec,
         ts[0].tv_nsec, ts[1].tv_sec, ts[1].tv_nsec, rv);
  return rv;
}

// Extended operations
int nufs_ioctl(const char *path, int cmd, void *arg, struct fuse_file_info *fi,
               unsigned int flags, void *data) {
  int rv = 0;
  printf("ioctl(%s, %d, ...) -> %d\n", path, cmd, rv);
  return rv;
}

int nufs_readlink(const char *path, char *buf, size_t size) {
  inode *parent_inode = get_inode(tree_lookup(path));

  char *filename = get_filename_from_path(path);
  inode *desired_inode = get_inode(directory_lookup(parent_inode, filename));

  nufs_read(path, buf, desired_inode->size, 0, NULL);
  return 0;
}

int nufs_symlink(const char *to, const char *from) {
  struct stat to_stat;
  int rv = 0;
  rv = nufs_getattr(to, &to_stat);
  if (rv < 0) {
    return rv;
  }

  rv = nufs_mknod(from, 0120644, (&to_stat)->st_rdev);
  if (rv < 0) {
    return rv;
  }
  inode *parent_inode = get_inode(tree_lookup(from));

  char *filename = get_filename_from_path(from);
  inode *desired_inode = get_inode(directory_lookup(parent_inode, filename));
  desired_inode->size = strlen(to);

  nufs_write(from, to, strlen(to), 0, NULL);

  printf("symlink success\n");

  return 0;
}

void nufs_init_ops(struct fuse_operations *ops) {
  memset(ops, 0, sizeof(struct fuse_operations));
  ops->access = nufs_access;
  ops->getattr = nufs_getattr;
  ops->readdir = nufs_readdir;
  ops->mknod = nufs_mknod;
  ops->mkdir = nufs_mkdir;
  ops->link = nufs_link;
  ops->unlink = nufs_unlink;
  ops->rmdir = nufs_rmdir;
  ops->rename = nufs_rename;
  ops->chmod = nufs_chmod;
  ops->truncate = nufs_truncate;
  ops->open = nufs_open;
  ops->read = nufs_read;
  ops->write = nufs_write;
  ops->utimens = nufs_utimens;
  ops->ioctl = nufs_ioctl;
  ops->symlink = nufs_symlink;
  ops->readlink = nufs_readlink;
};

struct fuse_operations nufs_ops;

void init_root() {
  /**
   *
   * |PAGE 0|
   *      ---------------------------------
   *     | Pages Bitmap (32 bytes, 256 bits)
   *     | ---------------------------------
   *     | Inode Bitmap (32 bytes, 256 bits)
   *     | ---------------------------------
   *     | Beggining of Inode Array (4032 bytes = 168 * sizeof(inode))
   *      ---------------------------------
   * |PAGE 1|
   *      ---------------------------------
   *     | Remainder of Inode Array (2112 bytes = 88 * sizeof(inode))
   *      ---------------------------------
   * |PAGE 2|
   *      ---------------------------------
   *     | ROOT DATA BLOCK (array of direntry, max_len 64)
   *      ---------------------------------
   *
   */

  void *inode_bitmap = get_inode_bitmap();
  void *pages_bitmap = get_pages_bitmap();

  // mark PAGE 1 in pages bitmap used for bitmaps and inode array
  bitmap_put(pages_bitmap, 1, 1);

  // mark INODE 0 in inode bitmap for root inode
  bitmap_put(inode_bitmap, 0, 1);

  inode *root_inode = get_root_inode();
  // initialize root inode
  root_inode->refs = 1;
  root_inode->mode = 040755;            // directory
  root_inode->size = sizeof(direntry);  // init to size of one direntry, itself

  // assign the page/block number of the root to first empty page
  // after pages bitmap, inode bitmap and inode array:
  int bytes = 32 + 32 + (256 * sizeof(inode));
  ROOT_PNUM = bytes_to_pages(bytes);

  // we know ROOT_PNUM is 2, mark used
  bitmap_put(pages_bitmap, ROOT_PNUM, 1);

  // the data block corresponding to the root
  void *root_block = pages_get_page(ROOT_PNUM);

  // making sure the root inode points to the root data block page num
  root_inode->ptrs[0] = ROOT_PNUM;

  // storing first direntry in root dir, itself,
  direntry *root_dirent = (direntry *)root_block;
  strcpy(root_dirent->name, ".\0");
  // root direntry coresponds to first inode
  root_dirent->inum = 0;
}

int main(int argc, char *argv[]) {
  assert(argc > 2 && argc < 6);

  pages_init(argv[--argc]);
  init_root();
  // storage_init(argv[--argc]);
  nufs_init_ops(&nufs_ops);
  return fuse_main(argc, argv, &nufs_ops, NULL);
}
