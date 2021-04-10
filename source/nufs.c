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

inode *get_root_inode() {
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
    direntry *curr_direntry = (direntry *)root_block;
    // only handling files in root directory
    // so we can just ignore first character "/"
    // and assume the rest is the filename
    char *desired_filename;
    strcpy(desired_filename, &path[1]);
    // assert(strcmp(desired_filename, "hello.txt") == 0);

    direntry *desired_direntry = NULL;
    int z = 0;
    while (curr_direntry) {
      z++;
      if (strcmp(desired_filename, curr_direntry->name) == 0) {
        desired_direntry = curr_direntry;
        break;
      }
      curr_direntry = curr_direntry->next;
    }
    // assert(z>1);
    if (desired_direntry == NULL) return -ENOENT;
    // bitmap_get(get_inode_bitmap(), desired_dirent
    int desired_inum = desired_direntry->inum;
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

  rv = nufs_getattr("/hello.txt", &st);
  assert(rv == 0);
  filler(buf, "hello.txt", &st, 0);

  printf("readdir(%s) -> %d\n", path, rv);
  return 0;
}

// mknod makes a filesystem object like a file or directory
// called for: man 2 open, man 2 link
int nufs_mknod(const char *path, mode_t mode, dev_t rdev) {
  int rv = -1;
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
  void *inode_bitmap = get_inode_bitmap();
  void *pages_bitmap = get_pages_bitmap();
  assert(bitmap_get(pages_bitmap, 0) == 1);
  // mark first element in inode bitmap as full
  bitmap_put(inode_bitmap, 0, 1);

  // set root inode to first address in inode array
  // ROOT_INODE = (inode*) (inode_bitmap + 32);

  inode *root_inode = get_root_inode();
  // initialize root inode
  root_inode->refs = 1;
  root_inode->mode = 040755;
  root_inode->size = 10;  // TODO:determine size of directory

  // assign the page number of the root to first empty page
  // after pages bitmap, inode bitmap and inode array:
  // size of pages bitmap 32 bytes (32*8 bits = 256 bits) plus
  // size of inode bitmap 32 bytes plus
  // size of inode array 256 * sizeof(inode)
  int bytes = 32 + 32 + (256 * sizeof(inode));
  ROOT_PNUM = bytes_to_pages(bytes);

  // the data block corresponding to the root
  void *root_block = pages_get_page(ROOT_PNUM);
  // making sure the root inode points to the root data block inum

  root_inode->ptrs[0] = 0;

  // storing first direntry in root dir, dir itself,
  direntry *root_dirent = (direntry *)root_block;

  strcpy(root_dirent->name, ".");
  // first directory entry is itself, TODO:
  // name it "/" or "."
  root_dirent->inum = 0;
}

int main(int argc, char *argv[]) {
  assert(argc > 2 && argc < 6);
  // printf("TODO: mount %s as data file\n", argv[--argc]);
  // char *path = argv[--argc];
  // puts(path);
  pages_init(argv[--argc]);
  init_root();
  // storage_init(argv[--argc]);
  nufs_init_ops(&nufs_ops);
  return fuse_main(argc, argv, &nufs_ops, NULL);
}
