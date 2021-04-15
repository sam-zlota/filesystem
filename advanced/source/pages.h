// based on cs3650 starter code

#ifndef PAGES_H
#define PAGES_H

#include <stdio.h>

static int PAGE_SIZE = 4096;

void pages_init(const char* path);
void pages_free();
void* pages_get_page(int pnum);
void* get_pages_bitmap();
void* get_inode_bitmap();
int alloc_page();
// int alloc_inum();
void free_page(int pnum);
void free_inode(int inum);

#endif
