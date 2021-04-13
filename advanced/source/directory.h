// based on cs3650 starter code

#ifndef DIRECTORY_H
#define DIRECTORY_H

#define DIR_NAME 48

// #include "slist.h"
#include "inode.h"
#include "pages.h"
#include "slist.h"

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
void print_directory(inode* dd);

#endif
