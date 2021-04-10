

#include "inode.h"

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
// int grow_inode(inode* node, int size);
// int shrink_inode(inode* node, int size);
// int inode_get_pnum(inode* node, int fpn);

inode *get_root_inode() {
  // first address in inode array
  // root inode comes 32 bytes (256 bits) after the beggining of inode bitmap
  void *inode_bitmap_addr = (void *)get_inode_bitmap();
  return (inode *)inode_bitmap_addr + 32;
}

inode *get_inode(int inum) { return get_root_inode + inum; }
