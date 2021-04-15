

#include "inode.h"

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
// int shrink_inode(inode* node, int size);
// int inode_get_pnum(inode* node, int fpn);

inode *get_root_inode() {
  // first address in inode array
  // root inode comes 32 bytes (256 bits) after the beggining of inode bitmap
  void *inode_bitmap_addr = (void *)get_inode_bitmap();
  return (inode *)(inode_bitmap_addr + 32);
}

inode *get_inode(int inum) { return get_root_inode() + inum; }

// TODO: need to comment out so shit compiles

// // Grows the inode to the given size
// // Returns 0 if successful, -1 if not
// int grow_inode(inode *node, int size) {
//   node->size += size;

//   // This variable represents how much more space we've allocated for the
//   inode.
//   // When this number is >= size, then we know we should return 0.
//   int newly_allocated_space = 0;

//   if (node->size > PAGE_SIZE && node->ptrs[1] == NULL) {
//     node->ptrs[1] = alloc_page();
//     newly_allocated_space += PAGE_SIZE;
//   }

//   if (node->size > 2 * PAGE_SIZE) {
//     // Check to see if we need to create the iptr
//     if (node->iptr == NULL) {
//       node->iptr = alloc_page();
//     }

//     // Find where the next pointer should go
//     for (int ii = 0; ii < PAGE_SIZE / sizeof(int); ii++) {
//       // If we still haven't allocated enough at this point
//       if (size > newly_allocated_space) {
//         memcpy((int *)pages_get_page(node->iptr) + ii, alloc_page(),
//                sizeof(int));

//         // Now check if size is <= newly_allocated_space
//         if (size <= newly_allocated_space) {
//           return 0;
//         }
//       }
//     }

//     // If we haven't returned yet, we're out of space
//     return -1;
//   }

//   return 0;
// }
