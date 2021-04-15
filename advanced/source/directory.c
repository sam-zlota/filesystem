// based on cs3650 starter code
#define DIR_NAME 48

#include <errno.h>
#include <string.h>
#include "directory.h"

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
int directory_lookup(inode* dd, const char* name) {
  printf("entered directory lookup\n");
  int rv = -1;
  // You're asking me to lookup the root in the root, so just return the root
  
  int ptr_index = -1;
  int page_index = dd->ptrs[0];
  direntry* entries = (direntry*)pages_get_page(page_index);

  if (strcmp(name, "") == 0) {
    rv = entries[0].inum;
    printf("exited directory lookup -> %ld\n", rv);
    return rv;
  }

  while (1) {
    entries = (direntry*)pages_get_page(page_index);
    for (int ii = 0; ii < min(dd->size / sizeof(direntry) + 1, MAX_DIRENTRIES);
         ii++) {
      if (strcmp(entries[ii].name, name) == 0) {
          rv = entries[ii].inum;
          printf("exited directory lookup -> %ld\n", rv);
          return rv;
      }
    }

    // Enumerate out the possibilities for where our page index could be,
    // starting from ptrs[0] and going onto the extra ptrs[] block
    if (page_index == dd->ptrs[0] && dd->ptrs[1] != 0) {
      page_index = dd->ptrs[1];
    } else if (page_index == dd->ptrs[1] && dd->iptr != 0) {

      page_index = *(int *)pages_get_page(dd->iptr);
      ptr_index = 0;
    } else if (ptr_index >= 0 &&
               ptr_index <
                   IPTR_PAGE_SIZE) {  
      ptr_index++;
      page_index = *(((int *) pages_get_page(dd->iptr)) + ptr_index);
      //TODO:
      //if page_index == 0, then we are at the end of this directory 

    } else {
      //-1 not found
      //TODO: -ENOENT
      printf("exited directory lookup -> %ld\n", rv);
      return rv;
    }
  }
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

  rv = first_free_entry_in_block(dd->ptrs[0]);


  if (rv > 0) {
    return &(((direntry*)pages_get_page(dd->ptrs[0]))[rv]);
  }
  // So it didn't pan out for ptrs[0]... Let's see if it's in ptrs[1]
  if (dd->ptrs[1] == 0) // First check that there's anything in ptrs[1]
  {
    // Grow ptrs[1] if it turns out that there's nothing in it
    // Getting to this point in the code means that ptrs[1] is full

    rv = grow_inode(dd, sizeof(direntry));
    if(rv < 0) {
      return NULL;
    }
    }

  rv = first_free_entry_in_block(dd->ptrs[1]);
  if (rv > 0) {
    return &(((direntry*)pages_get_page(dd->ptrs[1]))[rv]);
  }

  // Grow to iptr if it turns out that that's necessary
  if (dd->iptr == 0)
  {
    rv = grow_inode(dd, sizeof(direntry));
    if(rv < 0) {
      return NULL;
    }
  }

  int* iptr_page = pages_get_page(dd->iptr);
  int curr_pnum = *iptr_page;
  rv = first_free_entry_in_block(curr_pnum);
  int iptr_index = 1;

  if (rv > 0) {
    return &(((direntry*)pages_get_page(dd->ptrs[1]))[rv]);
  }


  while(iptr_index < 255) { 
    //pages 0, 1, 2 allocated, two pages directly, one page indirectly 
    //grow inode will handle

    curr_pnum  = *(iptr_page + iptr_index);

    if(curr_pnum == 0) {
      rv = grow_inode(dd, sizeof(direntry));
        if(rv < 0) {
          return NULL;
       }
      continue;
    }

    rv = first_free_entry_in_block(curr_pnum);

    if (rv > 0) {
        return &(((direntry*)pages_get_page(curr_pnum))[rv]);
    }
    iptr_index++;
  }
  

  return NULL;
}

// Puts an inum with the given name in the given parent directory, returning the
// new directory's inum
int directory_put(inode* dd, const char* name, int inum) {
  direntry* new_dirent = first_free_entry(dd);
  

  // direntry *first_empty_direntry = (direntry *)&direntry_arr[ii];
  // int first_free_inum = alloc_inum();
  // if (first_free_inum == -1) {
  //   return rv;
  // }


}

/*
// Puts an inum with the given name in the given parent directory, returning the
// new directory's inum
int directory_put(inode* dd, const char* name, int inum) {
  printf("entered directory put\n");
  int rv = -1;

  int ptr_index = -1;
  int page_index = dd->ptrs[0];
  direntry* entries = (direntry*)pages_get_page(page_index);
  int next_free_direntry_index = -1;
   
  while (1) {
    entries = (direntry*)pages_get_page(page_index);
    for (int ii = 0; ii < min(dd->size / sizeof(direntry) + 1, MAX_DIRENTRIES);
         ii++) {
           if(entries[ii].inum == 0 && page_index != 2) {
             //if inum zero and is not the root inode
             next_free_direntry_index = ii;
             break;
          }
    }
    if(next_free_direntry_index > 0) {
      printf("found a free direntry\n");
      break;
    }
    // Enumerate out the possibilities for where our page index could be,
    // starting from ptrs[0] and going onto the extra ptrs[] block
    if (page_index == dd->ptrs[0] && dd->ptrs[1] != 0) {
      page_index = dd->ptrs[1];
    } else if (page_index == dd->ptrs[1] && dd->iptr != 0) {

      page_index = *(int *)pages_get_page(dd->iptr);
      //check if 0
      ptr_index = 0;
    } else if (ptr_index >= 0 &&
               ptr_index <
                   IPTR_PAGE_SIZE) {  // TODO this needs to be fixed, later
      ptr_index++;
      //5 7 8 88 76 0 0 0 0 0 0
      page_index = *(((int *) pages_get_page(dd->iptr)) + ptr_index);
      if(page_index == 0) {
        //TODO: inode grow
        //allocate a new page, add it to dd->iptr
        //if out of disk space return -ENOSPC;
        printf("need to map a new page\n");
      }
    } else {
      //no more space
      //TODO: we need to grow the directory
      //
      printf("just checked the last page (255)")
      return -ENOSPC;
    }
  }

  direntry *first_empty_direntry = (direntry *)&entries[next_free_direntry_index];
  // int first_free_inum = alloc_inum();
  // if (first_free_inum == -1) {
  //   return rv;
  // }

  first_empty_direntry->inum = inum;
  strcat(first_empty_direntry->name, name);
  dd->size += sizeof(direntry);

  //TODO: what if its a directory
  inode *new_inode = get_inode(inum);

  new_inode->mode = 100644;
  new_inode->refs = 1;
  new_inode->size = 0;
  int first_free_pnum = alloc_page();
  if (first_free_pnum == -1) {
    printf("pnum error\n");
    return -1;
  }
  printf("successfully exited directory put\n");
  return 0;
}*/
