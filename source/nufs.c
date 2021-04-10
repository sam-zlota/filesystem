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
#include <sys/types.h>
#include <unistd.h>

#define FUSE_USE_VERSION 26
#include <fuse.h>

#include "bitmap.h"
#include "directory.h"
#include "pages.h"
#include "util.h"

int ROOT_PNUM = -1;
// a directory cannot have more than 64 entries
// 64*sizeof(direntry) = 64*64 = 4096
static int MAX_DIRENTRIES = 64;  // TODO: change because changed size of
                                 // direntry

inode *get_root_inode() {
  // first address in inode array
  // root inode comes 32 bytes (256 bits) after the beggining of inode bitmap
  void *inode_bitmap_addr = (void *)get_inode_bitmap();
  return (inode *)inode_bitmap_addr + 32;
}

// implementation for: man 2 access
// Checks if a file exists.
int nufs_access(const char *path, int mask) {
  int rv = 0;
  printf("access(%s, %04o) -> %d\n", path, mask, rv);
  return rv;
}

// implementation for: man 2 stat
// gets an object's attributes (type, permissions, size, etc)
int nufs_getattr(const char *path, struct stat *st) {
  int rv = 0;
  memset(st, 0, sizeof(stat));
  inode *root_inode = get_root_inode();

  if (strcmp(path, "/") == 0) {
    st->st_mode = root_inode->mode;  // directory
    st->st_size = root_inode->size;
    st->st_uid = getuid();
    return rv;
  } else {
    void *root_block = pages_get_page(ROOT_PNUM);
    direntry *direntry_arr = (direntry *)root_block;
    // only handling files in root directory
    // so we can just ignore first character "/"
    // and assume the rest is the filename
    char *desired_filename;
    strcpy(desired_filename, &path[1]);

    // iterate over direntry_arr
    int ii;
    int not_found = 1;
    for (ii = 1; ii < MAX_DIRENTRIES; ii++) {
      if (direntry_arr[ii].inum == 0) {
        // 0 is reserved for root or uninitialzied, so we must have
        // reached end of array, because array is contiguous and we
        // are not searching for root
        break;
      }
      if (strcmp(desired_filename, direntry_arr[ii].name) == 0) {
        // desired dir entry is at index ii
        not_found = 0;
        break;
      }
    }
    if (not_found) return -ENOENT;
    direntry desired_direntry = direntry_arr[ii];

    // bitmap_get(get_inode_bitmap(), desired_dirent
    int desired_inum = desired_direntry.inum;
    // pointer arithmetic
    inode *desired_inode = root_inode + desired_inum;
    st->st_mode = desired_inode->mode;  //  0100644; // regular file
    st->st_size = desired_inode->size;
    st->st_uid = getuid();
    return rv;
  }

  printf("getattr(%s) -> (%d) {mode: %04o, size: %ld}\n", path, rv, st->st_mode,
         st->st_size);
  return rv;
}

// implementation for: man 2 readdir
// lists the contents of a directory
int nufs_readdir(const char *path, void *buf, fuse_fill_dir_t filler,
                 off_t offset, struct fuse_file_info *fi) {
  struct stat st;
  int rv;

  rv = nufs_getattr("/", &st);
  assert(rv == 0);

  filler(buf, ".", &st, 0);

  // rv = nufs_getattr("/hello.txt", &st);
  // assert(rv == 0);
  // filler(buf, "hello.txt", &st, 0);

  printf("readdir(%s) -> %d\n", path, rv);
  return 0;
}

// mknod makes a filesystem object like a file or directory
// called for: man 2 open, man 2 link
int nufs_mknod(const char *path, mode_t mode, dev_t rdev) {
  int rv = -1;
  char *desired_filename;
  strcpy(desired_filename, &path[1]);

  inode *root_inode = get_root_inode();
  void *root_data = pages_get_page(root_inode->ptrs[0]);
  direntry *direntry_arr = (direntry *)root_data;

  int ii;
  int not_found = 1;
  // TODO: handle duplicates?
  for (ii = 1; ii < MAX_DIRENTRIES; ii++) {
    if (direntry_arr[ii].inum == 0) {
      // 0 is reserved for root or uninitialzied, so we must have
      // reached end of array, because array is contiguous and we
      // are not searching for root
      not_found = 0;
      break;
    }
    if (strcmp(desired_filename, direntry_arr[ii].name) == 0) {
      // desired dir entry is at index ii
      return -EEXIST;
    }
  }
  if (not_found) return -EDQUOT;  // directory space

  direntry *first_empty_direntry = (direntry *)&direntry_arr[ii];
  int first_free_inum = alloc_inum();
  if (first_free_inum == -1) {
    return rv;
  }
  first_empty_direntry->inum = first_free_inum;
  first_empty_direntry->name = desired_filename;
  root_inode->size += sizeof(direntry);

  inode *new_inode = get_inode(first_empty_direntry->inum);

  new_inode->mode = mode_t;
  new_inode->refs = 1;
  new_inode->size = 0;
  int first_free_pnum = alloc_page();
  if (first_free_pnum == -1) {
    return rv;
  }
  new_inode->ptrs[0] = first_free_pnum;
  rv = 0;
  printf("mknod(%s, %04o) -> %d\n", path, mode, rv);
  return rv;
}

// most of the following callbacks implement
// another system call; see section 2 of the manual
int nufs_mkdir(const char *path, mode_t mode) {
  int rv = nufs_mknod(path, mode | 040000, 0);
  printf("mkdir(%s) -> %d\n", path, rv);
  return rv;
}

int nufs_unlink(const char *path) {
  int rv = -1;
  printf("unlink(%s) -> %d\n", path, rv);
  return rv;
}

int nufs_link(const char *from, const char *to) {
  int rv = -1;
  printf("link(%s => %s) -> %d\n", from, to, rv);
  return rv;
}

int nufs_rmdir(const char *path) {
  int rv = -1;
  printf("rmdir(%s) -> %d\n", path, rv);
  return rv;
}

// implements: man 2 rename
// called to move a file within the same filesystem
int nufs_rename(const char *from, const char *to) {
  int rv = -1;
  printf("rename(%s => %s) -> %d\n", from, to, rv);
  return rv;
}

int nufs_chmod(const char *path, mode_t mode) {
  int rv = -1;
  printf("chmod(%s, %04o) -> %d\n", path, mode, rv);
  return rv;
}

int nufs_truncate(const char *path, off_t size) {
  int rv = -1;
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
  int rv = 6;
  strcpy(buf, "hello\n");
  printf("read(%s, %ld bytes, @+%ld) -> %d\n", path, size, offset, rv);
  return rv;
}

// Actually write data
int nufs_write(const char *path, const char *buf, size_t size, off_t offset,
               struct fuse_file_info *fi) {
  int rv = -1;
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
  int rv = -1;
  printf("ioctl(%s, %d, ...) -> %d\n", path, cmd, rv);
  return rv;
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

  // mark second element in pages bitmap used for bitmaps and inode array
  bitmap_put(pages_bitmap, 1, 1);

  // mark first element in inode bitmap for root inode
  bitmap_put(inode_bitmap, 0, 1);

  inode *root_inode = get_root_inode();
  // initialize root inode
  root_inode->refs = 1;
  root_inode->mode = 040755;            // directory
  root_inode->size = sizeof(direntry);  //

  // assign the page number of the root to first empty page
  // after pages bitmap, inode bitmap and inode array:
  int bytes = 32 + 32 + (256 * sizeof(inode));
  ROOT_PNUM = bytes_to_pages(bytes);

  // we know ROOT_PNUM is 2, mark used
  bitmap_put(pages_bitmap, ROOT_PNUM, 1);

  // the data block corresponding to the root
  void *root_block = pages_get_page(ROOT_PNUM);

  // making sure the root inode points to the root data block page num
  root_inode->ptrs[0] = ROOT_PNUM;
  memset(root_block, 0, 4096);
  // storing first direntry in root dir, itself,
  direntry *root_dirent = (direntry *)root_block;
  strcpy(root_dirent->name, ".");
  // root direntry coresponds to first inode
  root_dirent->inum = 0;
}

int main(int argc, char *argv[]) {
  assert(argc > 2 && argc < 6);
  // printf("TODO: mount %s as data file\n", argv[--argc]);
  // char *path = argv[--argc];
  // puts(path);
  pages_init(argv[--argc]);
  init_root();
  printf("sizeof(direntry) = %ld\n", sizeof(direntry));
  // storage_init(argv[--argc]);
  nufs_init_ops(&nufs_ops);
  return fuse_main(argc, argv, &nufs_ops, NULL);
}
