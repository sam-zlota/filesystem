// based on cs3650 starter code
#define DIR_NAME 48

#include "directory.h"
#include "inode.h"
#include "pages.h"
#include "slist.h"
#include "util.h"

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
int directory_lookup(inode* dd, const char* name)
{   
    direntry* entries = (direntry*)pages_get_page(dd->ptrs[0] + 2);

    int ii = 0;

    while (ii < min(dd->size, PAGE_SIZE))
    {
        if (strcmp(entries[ii].name, name) == 0)
        {
          return entries[ii].inum;
        }
        ii++;
    }

    return -1;
}

// Returns the parent of this path
int tree_lookup(const char* path) {
  slist* delim_path = s_split(strdup(path) + 1, '/');
  int curr_dir = 0;

  // This means that we are at the root node
  if (delim_path->next == NULL)
  {
    return 0;
  }

  // Loop through the deliminated path, terminating only when you are the parent of the last
  // node in the list
  while (delim_path->next->next)
  {
    curr_dir = directory_lookup(get_inode(curr_dir), delim_path->data);
    delim_path = delim_path->next;
  }

  return curr_dir;
}

// Get the first free entry in the given directory block
direntry* first_free_entry_in_block(int pnum)
{
  direntry* page = (direntry*)pages_get_page(pnum);
  
}

direntry* first_free_entry(inode* dd)
{
  
}

// Puts an inum with the given name in the given parent directory, returning the new directory's inum
int directory_put(inode* dd, const char* name, int inum)
{
  

  return 0;
}