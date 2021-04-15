// based on cs3650 starter code
#define DIR_NAME 48

#include "directory.h"

#include <errno.h>
#include <string.h>

#include "inode.h"
#include "pages.h"
#include "slist.h"
#include "util.h"

static int IPTR_PAGE_SIZE = 4096 / sizeof(int);

/*
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
void print_directory(inode* dd);*/

// Returns inode for the given file name in the given directory
// Returns -1 if we can't find it

int find_in_block(int pnum, char* name) {
  direntry* block = (direntry*)pages_get_page(pnum);
  int ii = 0;
  while (ii < MAX_DIRENTRIES) {
    direntry* curr_dirent = &block[ii];

    if (strcmp(curr_dirent->name, name) == 0) {
      return ii;
    }
  }

  return -1;
}

int directory_lookup(inode* dd, const char* name) {
  printf("entered directory lookup\n");
  int rv = -1;
  // You're asking me to lookup the root in the root, so just return the root

  int curr_pnum = dd->ptrs[0];
  int iptr_index = -1;
  int* iptr_page = (int*)pages_get_page(dd->iptr);

  // this will run until it finds matching direntry or checks all direntries
  while (find_in_block(curr_pnum, name) < 0) {
    if (iptr_index < 0)
      curr_pnum = dd->ptrs[1];
    else
      curr_pnum = *(iptr_page + iptr_index);

    if (curr_pnum == 0) {
      // have reached end
      return -ENOENT;
    }
    iptr_index++;
  }

  int direntry_index = find_in_block(curr_pnum, name);
  direntry* curr_directory = pages_get_page(curr_pnum);
  direntry* desired_direntry = &curr_directory[direntry_index];
  return desired_direntry->inum;
  ;
}

// Returns the parent of this path
int tree_lookup(const char* path) {
  printf("entered tree lookup\n");
  slist* delim_path = s_split(strdup(path), '/');
  int curr_dir = 0;

  // This means that we are at the root node
  if (delim_path->next == NULL) {
    printf("returning zero\n");
    return 0;
  }

  // Loop through the deliminated path, terminating only when you are the parent
  // of the last node in the list
  while (delim_path->next->next != NULL) {
    curr_dir = directory_lookup(get_inode(curr_dir), delim_path->data);
    delim_path = delim_path->next;
  }

  return curr_dir;
}

// Helper function for first_free_entry
// Get the first free entry in the given directory block

int first_free_entry_in_block(int pnum) {
  direntry* page = (direntry*)pages_get_page(pnum);

  int ii = 0;

  while (ii < MAX_DIRENTRIES) {
    if (page[ii].inum == 0 && pnum != 2) {
      return ii;
    }
  }

  return -1;
}

// Gets the first free entry at a given inode
direntry* first_free_entry(inode* dd) {
  int rv = 0;
  int curr_pnum = dd->ptrs[0];
  int iptr_index = -1;
  int* iptr_page = (int*)pages_get_page(dd->iptr);

  // this will run until it finds free block or runs out of memory
  while (first_free_entry_in_block(curr_pnum) < 0) {
    if (iptr_index < 0)
      curr_pnum = dd->ptrs[1];
    else
      curr_pnum = *(iptr_page + iptr_index);

    if (curr_pnum == 0) {
      rv = grow_inode(dd, sizeof(direntry));
      if (rv < 0) {
        // we ran out of memory
        return NULL;
      }
      // should now check break from while condition, becasue we
      // succesfully allocated a new page
    }

    iptr_index++;
  }

  int direntry_index = first_free_entry_in_block(curr_pnum);
  direntry* curr_directory = pages_get_page(curr_pnum);
  direntry* first_free = &curr_directory[direntry_index];

  return first_free;
}

// Puts an inum with the given name in the given parent directory, returning the
// new directory's inum
int directory_put(inode* dd, const char* name, int inum) {
  direntry* new_dirent = first_free_entry(dd);

  if (new_dirent == NULL) {
    return -ENOSPC;
  }

  new_dirent->inum = inum;
  strcpy(new_dirent->name, name);

  return 0;
}

// this should only be called when there are no more links/refs
int directory_delete(inode* dd, const char* name) {
  assert(dd->refs == 0);

  inode* parent = tree_lookup
}