// based on cs3650 starter code
#define DIR_NAME 48

#include "directory.h"

#include <assert.h>
#include <errno.h>
#include <string.h>
#include <sys/stat.h>

#include "inode.h"
#include "pages.h"
#include "slist.h"
#include "util.h"

// returns direntry index of direntry with name at block at page pnum
int find_in_block(int pnum, const char* name) {
  direntry* block = (direntry*)pages_get_page(pnum);
  int ii = 0;
  while (ii < MAX_DIRENTRIES) {
    direntry* curr_dirent = &block[ii];
    if (strcmp(curr_dirent->name, name) == 0) {
      return ii;
    }
    ii++;
  }
  return -1;
}

// returns the inum of the direntry in this directory with name
int directory_lookup(inode* dd, const char* name) {
  int rv = -1;
  // You're asking me to lookup the root in the root, so just return the root
  int curr_pnum = dd->ptrs[0];
  int iptr_index = -1;
  int* iptr_page = (int*)pages_get_page(dd->iptr);

  // this handles the case "" is given
  if (strcmp("\0", name) == 0) {
    // it means we want

    return 0;
  }

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
  printf("exited directory lookup: success\n");
  return desired_direntry->inum;
}

// Returns the inum of parent directory of the filename at the end of the path
int tree_lookup(const char* path) {
  slist* delim_path = s_split(strdup(path), '/');
  int curr_dir = 0;

  // This means that we are at the root node
  if (delim_path->next == NULL) {
    printf("tree lookup exiting, success found root\n");
    return 0;
  }

  // Loop through the deliminated path, terminating only when you are the parent
  // of the last node in the list
  while (delim_path->next->next != NULL) {
    delim_path = delim_path->next;
    curr_dir = directory_lookup(get_inode(curr_dir), delim_path->data);
  }

  printf("tree lookup exiting, success\n");
  return curr_dir;
}

// returns direntry index of direntry with inum == 0, which means it is free
int first_free_entry_in_block(int offset, int pnum) {
  direntry* page = (direntry*)pages_get_page(pnum);
  int ii = offset;
  while (ii < MAX_DIRENTRIES) {
    if (page[ii].inum == 0 && !(pnum == 2 && ii == 0)) {
      return ii;
    }
    ii++;
  }
  return -1;
}

// Puts an inum with the given name in the given parent directory
int directory_put(inode* dd, const char* name, int inum) {
  int rv = 0;
  int curr_pnum = dd->ptrs[0];
  int iptr_index = -1;
  int* iptr_page = (int*)pages_get_page(dd->iptr);

  int offset = 1;
  // If it ain't the root
  if (dd->ptrs[0] != 2) {
    offset = 2;
  }

  // this will run until it finds free block or runs out of memory
  while (first_free_entry_in_block(offset, curr_pnum) < 0) {
    if (iptr_index < 0)
      curr_pnum = dd->ptrs[1];
    else
      curr_pnum = *(iptr_page + iptr_index);

    if (curr_pnum == 0) {
      // should always grow by a full page TODO:
      rv = grow_inode(dd, sizeof(direntry));
      if (rv < 0) {
        // we ran out of memory
        printf("exiting directory put: failure\n");
        return -ENOSPC;
      }
      // should now check break from while condition, becasue we
      // succesfully allocated a new page
    }

    iptr_index++;
  }

  int direntry_index = first_free_entry_in_block(offset, curr_pnum);
  direntry* curr_directory = pages_get_page(curr_pnum);
  direntry* new_dirent = &curr_directory[direntry_index];

  new_dirent->inum = inum;
  strcpy(new_dirent->name, name);

  dd->size += sizeof(direntry);

  assert(strcmp(new_dirent->name, name) == 0);
  // new_dirent->name = buf;
  printf("exiting directory put: success\n");
  return 0;
}

// helper to determine if the inode should free this block
int is_block_empty(int pnum) {
  direntry* block = (direntry*)pages_get_page(pnum);

  int ii = 0;

  while (ii < MAX_DIRENTRIES) {
    direntry* curr_dirent = &block[ii];
    if (curr_dirent->inum > 0) {
      return 0;
    }
    ii++;
  }
  return 1;
}

// Deletes a single file
int delete_file(direntry* desired_direntry, inode* desired_inode,
                int curr_pnum) {
  // Delete the inode if refs = 0
  if (desired_inode->refs == 0) {
    free_inode(desired_direntry->inum);
  }

  memset(desired_direntry, 0, sizeof(direntry));
  // assert that it has been delelted
  assert(desired_direntry->inum == 0);

  // TODO
  // check to see if any more
  if (is_block_empty(curr_pnum)) {
    // inode_shrink();
  }

  printf("exiting directory delete: success\n");
  return 0;
}

// this should only be called when there are no more links/refs, deletes
// direntry in directory with name
int directory_delete(inode* dd, const char* name) {
  // assert(dd->refs == 0);

  int curr_pnum = dd->ptrs[0];
  int iptr_index = -1;
  int* iptr_page = (int*)pages_get_page(dd->iptr);

  // this will run until it finds matching direntry or checks all direntries
  while (find_in_block(curr_pnum, name) < 0) {
    if (iptr_index < 0) {
      curr_pnum = dd->ptrs[1];
    } else {
      curr_pnum = *(iptr_page + iptr_index);
    }

    if (curr_pnum == 0) {
      // have reached end of this directory
      printf(" exiting directory delete: failure\n");
      return -ENOENT;
    }
    iptr_index++;
  }

  int direntry_index = find_in_block(curr_pnum, name);
  direntry* curr_directory = (direntry*)pages_get_page(curr_pnum);
  direntry* desired_direntry = &curr_directory[direntry_index];
  inode* desired_inode = get_inode(desired_direntry->inum);

  int mode = desired_inode->mode & S_IFMT;
  int filemode = S_IFREG;

  desired_inode->refs--;

  return delete_file(desired_direntry, desired_inode, curr_pnum);
}

// cons each of the names of the direntries at page with the rest
slist* cons_page_contents(int pnum, int starting_index, slist* rest) {
  direntry* block = (direntry*)pages_get_page(pnum);
  slist* contents = rest;
  int ii = starting_index;
  while (ii < MAX_DIRENTRIES) {
    direntry* curr_dirent = &block[ii];
    if (curr_dirent->inum != 0) {
      contents = s_cons(curr_dirent->name, contents);
    }
    ii++;
  }
  printf("exiting cons page\n");
  return contents;
}

// will put all the contents of directory into an slist
slist* directory_list(const char* path) {
  char* fname = get_filename_from_path(path);

  int parent_inum = tree_lookup(path);
  inode* dd = get_inode(directory_lookup(get_inode(parent_inum), fname));
  int curr_pnum = dd->ptrs[0];

  // TODO refactor this
  slist* contents = s_cons(".", NULL);
  if (dd->ptrs[0] == 2) {
    contents = cons_page_contents(curr_pnum, 1, contents);
  } else {
    contents = cons_page_contents(curr_pnum, 2, contents);
  }

  int iptr_index = 0;
  int* iptr_page = (int*)pages_get_page(dd->iptr);
  curr_pnum = dd->ptrs[1];

  // this will run until it finds matching direntry or checks all direntries
  while (curr_pnum != 0) {
    contents = cons_page_contents(curr_pnum, 0, contents);
    curr_pnum = *(iptr_page + iptr_index);
    iptr_index++;
  }

  printf("successfully exited directory list\n");
  return s_reverse(contents);
}