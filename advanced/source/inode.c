

#include "inode.h"

#include <assert.h>
#include <errno.h>
#include <stdio.h>
#include <string.h>

#include "bitmap.h"
#include "pages.h"
#include "util.h"


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


  int *iptr_arr = (int *)pages_get_page(node->iptr);
  int iptr_index = 0;
  while (pages_needed > 0 && iptr_index < 256) {
    if (node->ptrs[1] == 0) {
      node->ptrs[1] = alloc_page();
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
      pages_needed--;
    }

    iptr_index++;
  }
  if (pages_needed > 0) {
    return -ENOSPC;
  }

  return 0;
}

// Shrinks the inode to the given size
int shrink_inode(inode *node, int size) {
  node->size -= size;
  // This is the number of pages we need to leave allocated. It gets decremented as we go through page_targets
  int dealloc_target = size / PAGE_SIZE;
  int dealloc_space = 0;
  int page_target = node->ptrs[0];
  int *ppage_target = &node->ptrs[0];
  int iptr_index = -1;

  while (1)
  {
    // The first page that we need to dealloc is the weird case where we might not have to deallocate the whole page
    if (dealloc_target == 0)
    {
      void* target_address = (char*)pages_get_page(page_target) + (size % PAGE_SIZE);
      memset(target_address, 0, PAGE_SIZE - (size % PAGE_SIZE));

      // Reason why we can't have size == 0 is because then we'd try to set iptrs[0] = 0, and that's no good
      if ((size % PAGE_SIZE) == 0 && size != 0)
      {
        free_page(page_target);
        memset(ppage_target, 0, sizeof(int));
      }
    }
    
    // Subsequently, we deallocate the whole page
    if (dealloc_target < 0)
    {
      free_page(page_target);
      memset(ppage_target, 0, sizeof(int));
    }

    dealloc_target--;

    if (page_target == node->ptrs[0] && node->ptrs[1] != 0)
    {
      page_target = node->ptrs[1];
      ppage_target = &node->ptrs[1];
    }
    else if (page_target == node->ptrs[1] && node->iptr != 0)
    {
      iptr_index = 0;
      ppage_target = pages_get_page(node->iptr);
      page_target = *(int*)ppage_target;
    }
    else if (iptr_index >= 0 && iptr_index < (PAGE_SIZE / sizeof(int)))
    {
      iptr_index++;
      ppage_target = ((int*)pages_get_page(node->iptr) + iptr_index);
      page_target = *ppage_target;
    }
    else
    {
      break;
    }
  }

  return 0;
}

void coallesce_direntries(inode *node, int size) {
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
}

void free_inode(int inum)
{
  // Free the pages associated with this inode
  inode* target = get_inode(inum);
  
  if (target->ptrs[0] != 0)
    free_page(target->ptrs[0]);
  if (target->ptrs[1] != 0)
    free_page(target->ptrs[1]);
  if (target->iptr != 0)
  {
    int* iptr_arr = (int*)pages_get_page(target->iptr);
    int curr_pnum = -1;
    for (int ii = 0; ii < 256; ii++ )
    {
      curr_pnum = * (iptr_arr + ii);
      if(curr_pnum != 0)
        free_page(curr_pnum);
    }
    free_page(target->iptr);
  }

  // Memset the inode page as zero (just in case it's necessary)
  // memset(target, 0, sizeof(inode));

  // Mark the bitmap as the inode is occupied
  bitmap_put(get_inode_bitmap(), inum, 0);
}

