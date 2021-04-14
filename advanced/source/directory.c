// based on cs3650 starter code

#define DIR_NAME 48

// #include "slist.h"
#include "inode.h"
#include "pages.h"
#include "slist.h"

typedef struct direntry {
  char name[DIR_NAME];
  int inum;
} direntry;

void directory_init();
int directory_lookup(inode* dd, const char* name);
int tree_lookup(const char* path);
int directory_put(inode* dd, const char* name, int inum);
int directory_delete(inode* dd, const char* name);
slist* directory_list(const char* path);
void print_directory(inode* dd);

// returns the inum of this path
int tree_lookup(const char* path) {
  // return inum

  // inode *root_inode = get_root_inode();
  // hanlde non root dir

  if (strcmp(path, "/") == 0) {
    root_inode->mode = mode;  // directory
  } else {
    void* root_block = pages_get_page(ROOT_PNUM);
    direntry* direntry_arr = (direntry*)root_block;

    int ii;
    int not_found = 1;

    // Handle infinite dir enties
    for (ii = 0; ii < MAX_DIRENTRIES; ii++) {
      if (strcmp(path, direntry_arr[ii].name) == 0) {
        not_found = 0;
        break;
      }
    }

    if (not_found) {
      return -ENOENT;
    }

    direntry desired_direntry = direntry_arr[ii];

    int desired_inum = desired_direntry.inum;
  }
}