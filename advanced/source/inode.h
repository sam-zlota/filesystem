// based on cs3650 starter code

#ifndef INODE_H
#define INODE_H

#include <time.h>

#include "pages.h"

typedef struct inode {
  int refs;     // reference count
  int mode;     // permission & type
  int size;     // bytes
  int ptrs[2];  // direct pointers
  int iptr;     // single indirect pointer
  time_t accessed;
  time_t modified;
  time_t changed;
} inode;

void print_inode(inode* node);
inode* get_root_inode();
inode* get_inode(int inum);
int alloc_inode();
void free_inode(int inum);
int grow_inode(inode* node, int size);
int shrink_inode(inode* node, int size);
int inode_get_pnum(inode* node, int fpn);
// void set_curr_dir(int inum);

#endif
