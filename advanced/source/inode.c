

#include "inode.h"

#include <stdio.h>
#include <string.h>

#include "bitmap.h"
#include "pages.h"

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
  node->size += size;

  // This variable represents how much more space we've allocated for the
  // inode.
  // When this number is >= size, then we know we should return 0.
  int newly_allocated_space = 0;

  if (node->size > PAGE_SIZE && node->ptrs[1] == 0) {
    node->ptrs[1] = alloc_page();
    newly_allocated_space += PAGE_SIZE;
  }

  if (node->size > 2 * PAGE_SIZE) {
    // Check to see if we need to create the iptr
    if (node->iptr == 0) {
      node->iptr = alloc_page();
    }

    // Find where the next pointer should go
    for (int ii = 0; ii < PAGE_SIZE / sizeof(int); ii++) {
      // If we still haven't allocated enough at this point
      if (size > newly_allocated_space) {
        int pnum = alloc_page();
        memcpy((int *)pages_get_page(node->iptr) + ii, &pnum, sizeof(int));

        // Now check if size is <= newly_allocated_space
        if (size <= newly_allocated_space) {
          return 0;
        }
      }
    }

    // If we haven't returned yet, we're out of space
    return -1;
  }

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