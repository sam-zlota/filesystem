

#include "inode.h"

#include <assert.h>
#include <errno.h>
#include <stdio.h>
#include <string.h>

#include "bitmap.h"
#include "pages.h"
#include "util.h"

// typedef struct inode {
//   int refs;     // reference count
//   int mode;     // permission & type
//   int size;     // bytes
//   int ptrs[2];  // direct pointers
//   int iptr;     // single indirect pointer
// } inode;

// void print_inode(inode* node);
// inode* get_root_inode();
// inode* get_inode(int inum);
// int alloc_inode();
// void free_inode();
// int grow_inode(inode *node, int size) { return -1; }
// int shrink_inode(inode* node, int size);
// int inode_get_pnum(inode* node, int fpn);

int alloc_inode() {
  void *ibm = get_inode_bitmap();

  for (int ii = 1; ii < 256; ++ii) {
    if (!bitmap_get(ibm, ii)) {
      bitmap_put(ibm, ii, 1);
      inode *new_inode = get_inode(ii);
      int new_pnum = alloc_page();
      if (new_pnum < 0) {
        return new_pnum;
      }
      new_inode->ptrs[0] = new_pnum;
      return ii;
    }
  }

  return -1;
}

inode *get_root_inode() {
  // first address in inode array
  // root inode comes 32 bytes (256 bits) after the beggining of inode bitmap
  void *inode_bitmap_addr = (void *)get_inode_bitmap();
  return (inode *)(inode_bitmap_addr + 32);
}

inode *get_inode(int inum) { return get_root_inode() + inum; }

// Grows the inode to the given size
// Returns 0 if successful, -1 if not
int grow_inode(inode *node, int size) {
  int pages_needed = bytes_to_pages(size);

  assert(pages_needed > 0);

  printf("growing inode by %ld pages\n", pages_needed);

  int *iptr_arr = (int *)pages_get_page(node->iptr);
  int iptr_index = 0;
  while (pages_needed > 0 && iptr_index < 256) {
    if (node->ptrs[1] == 0) {
      node->ptrs[1] = alloc_page();
      // node->size +=4096;

      pages_needed--;
      continue;
    }
    if (node->iptr == 0) {
      node->iptr = alloc_page();
      iptr_arr = (int *)pages_get_page(node->iptr);
    }
    int *curr_pnum_ptr = iptr_arr + iptr_index;
    if (*curr_pnum_ptr == 0) {
      *curr_pnum_ptr = alloc_page();
      // node->size +=4096;
      printf("iprtr index: %ld, new page: %ld\n", iptr_index, *curr_pnum_ptr);
      pages_needed--;
    }

    iptr_index++;
  }
  if (pages_needed > 0) {
    return -ENOSPC;
  }

  // node->size += bytes_to_pages(size) * 4096;
  return 0;
}

// Shrinks the inode by the given size
int shrink_inode(inode *node, int size) {
  node->size -= size;
  int dealloc_space = 0;

  // We have a pointer to the current page and we have a pointer to the next
  // pointer so that whenever we detect a null page, we can shift the contents
  // of the next page down one
  int *ppointer = &node->ptrs[0];
  int *next_ppointer = NULL;
  int iptr_index = -1;

  while (1) {
    if (*ppointer == node->ptrs[0] && node->ptrs[1] != 0) {
      next_ppointer = &node->ptrs[1];
    } else if (*ppointer == node->ptrs[1] && node->iptr != 0) {
      next_ppointer = (int *)pages_get_page(node->iptr);
      iptr_index = 0;
    } else if (iptr_index >= 0 && iptr_index < PAGE_SIZE) {
      iptr_index++;
      next_ppointer = (int *)pages_get_page(node->iptr) + iptr_index;
    } else {
      // If it reaches here, that means that we're done
      break;
    }

    // If we detect a block is equal to zero, we should shift the contents of
    // the next pointer in, then nullify the next pointer
    if (*ppointer == 0) {
      *ppointer = *next_ppointer;
      *next_ppointer = 0;
    }

    ppointer = next_ppointer;
  }

  return 0;
}

// // Set the given inum as the current directory
// void set_curr_dir(int inum)
// {
//   int* curr_dir_inum = 88 * sizeof(inode);
//   *curr_dir_inum = 0;
// }