// based on cs3650 starter code
#define DIR_NAME 48

#include "directory.h"

#include <assert.h>
#include <errno.h>
#include <string.h>
#include <sys/stat.h>

#include "inode.h"
#include "pages.h"
#include "slist.h"
#include "util.h"

// returns direntry index of direntry with name at block at page pnum
int find_in_block(int pnum, const char* name) {
  direntry* block = (direntry*)pages_get_page(pnum);
  int ii = 0;
  printf("entered  find\n");
  while (ii < MAX_DIRENTRIES) {
    direntry* curr_dirent = &block[ii];
    printf("comparing: %s with %s, inum: %ld\n", curr_dirent->name, name, curr_dirent->inum);
    if (strcmp(curr_dirent->name, name) == 0) {
      printf("FOUND in block\n");
      return ii;
    }
    ii++;
  }
  return -1;
}

// returns the inum of the direntry in this directory with name
int directory_lookup(inode* dd, const char* name) {
  printf("entered directory lookup with name: %s\n", name);
  int rv = -1;
  // You're asking me to lookup the root in the root, so just return the root
  int curr_pnum = dd->ptrs[0];
  int iptr_index = -1;
  int* iptr_page = (int*)pages_get_page(dd->iptr);

  // this handles the case "" is given
  if (strcmp("\0", name) == 0) {
    // it means we want
    printf("exited directory lookup: success, found empty\n");

    return 0;
  }

  printf("calling find with pnum: %ld and name: %s\n", curr_pnum, name);
  // this will run until it finds matching direntry or checks all direntries
  while (find_in_block(curr_pnum, name) < 0) {
    if (iptr_index < 0)
      curr_pnum = dd->ptrs[1];
    else
      curr_pnum = *(iptr_page + iptr_index);

    if (curr_pnum == 0) {
      // have reached end
      printf("exited directory lookup: failure -ENONET\n");

      return -ENOENT;
    }
    iptr_index++;
  }

  int direntry_index = find_in_block(curr_pnum, name);
  direntry* curr_directory = pages_get_page(curr_pnum);
  direntry* desired_direntry = &curr_directory[direntry_index];
  printf("exited directory lookup: success\n");
  return desired_direntry->inum;
}

int get_curr_dir(const char* path)
{
  
}

// Returns the inum of parent directory of the filename at the end of the path
int tree_lookup(const char* path) {
  printf("entered tree lookup\n");
  slist* delim_path = s_split(strdup(path), '/');
  int curr_dir = 0;

  // This means that we are at the root node
  if (delim_path->next == NULL) {
    printf("tree lookup exiting, success found root\n");
    return 0;
  }

  // Loop through the deliminated path, terminating only when you are the parent
  // of the last node in the list
  while (delim_path->next->next != NULL) {
    delim_path = delim_path->next;
    curr_dir = directory_lookup(get_inode(curr_dir), delim_path->data);
  }

  printf("tree lookup exiting, success\n");
  return curr_dir;
}

// returns direntry index of direntry with inum == 0, which means it is free
int first_free_entry_in_block(int offset, int pnum) {
  direntry* page = (direntry*)pages_get_page(pnum);
  int ii = offset;
  while (ii < MAX_DIRENTRIES) {
    if (page[ii].inum == 0 && !(pnum == 2 && ii == 0)) {
      return ii;
    }
    ii++;
  }
  return -1;
}

// Puts an inum with the given name in the given parent directory
int directory_put(inode* dd, const char* name, int inum) {
  ("entered directory put with name: %s\n", name);

  int rv = 0;
  int curr_pnum = dd->ptrs[0];
  int iptr_index = -1;
  int* iptr_page = (int*)pages_get_page(dd->iptr);

  int offset = 1;
  // If it ain't the root
  if (dd->ptrs[0] != 2)
  {
    offset = 2;
  }

  // this will run until it finds free block or runs out of memory
  while (first_free_entry_in_block(offset, curr_pnum) < 0) {
    if (iptr_index < 0)
      curr_pnum = dd->ptrs[1];
    else
      curr_pnum = *(iptr_page + iptr_index);

    if (curr_pnum == 0) {
      // should always grow by a full page TODO:
      rv = grow_inode(dd, sizeof(direntry));
      if (rv < 0) {
        // we ran out of memory
        printf("exiting directory put: failure\n");
        return -ENOSPC;
      }
      // should now check break from while condition, becasue we
      // succesfully allocated a new page
    }

    iptr_index++;
  }

  int direntry_index = first_free_entry_in_block(offset, curr_pnum);
  direntry* curr_directory = pages_get_page(curr_pnum);
  direntry* new_dirent = &curr_directory[direntry_index];

  new_dirent->inum = inum;
  strcpy(new_dirent->name, name);

  dd->size += sizeof(direntry);
  
  
  assert(strcmp(new_dirent->name, name) == 0);
  // new_dirent->name = buf;
  printf("exiting directory put: success\n");
  return 0;
}

// helper to determine if the inode should free this block
int is_block_empty(int pnum) {
  direntry* block = (direntry*)pages_get_page(pnum);

  int ii = 0;

  while (ii < MAX_DIRENTRIES) {
    direntry* curr_dirent = &block[ii];
    if (curr_dirent->inum > 0) {
      return 0;
    }
    ii++;
  }
  return 1;
}

// Deletes a single file
int delete_file(direntry* desired_direntry, inode* desired_inode, int curr_pnum)
{
  // Delete the inode if refs = 0
  if (desired_inode->refs == 0)
  {
    free_inode(desired_direntry->inum);
  }

  memset(desired_direntry, 0, sizeof(direntry));
  // assert that it has been delelted
  assert(desired_direntry->inum == 0);

  // TODO
  // check to see if any more
  if (is_block_empty(curr_pnum)) {
    printf("calling inode shrink from directory delete\n");
    // inode_shrink();
  }

  printf("exiting directory delete: success\n");
  return 0;
}

int delete_folder(direntry* desired_direntry, inode* desired_inode, int curr_pnum)
{
  int pnum = desired_inode->ptrs[0];
  int iptr_index = 0;
  
  // Now wipe this direntry off the disk
  if (desired_inode->refs == 0)
  {
    // If pnum == 0, then we've reached the end of our useful direntries. If iptr_index > PAGE_SIZE/sizeof(int), our iptr_index has exceeded the bounds of one page
    while (iptr_index <= PAGE_SIZE/sizeof(int) && pnum != 0)
    {
      for (int index = 0; index < PAGE_SIZE/sizeof(direntry*); index++)
      {
        direntry* entry = (direntry*)pages_get_page(pnum) + index;
        if (strcmp(entry->name, ".") == 0 || strcmp(entry->name, "..") == 0)
        {
          continue;
        }
        directory_delete(desired_inode, entry->name);
      }

      if (pnum == desired_inode->ptrs[0] && desired_inode->ptrs[1] != 0)
      {
        pnum = desired_inode->ptrs[1];
      }
      else if (pnum == desired_inode->ptrs[1] && desired_inode->iptr != 0)
      {
        pnum = *((int*)pages_get_page(desired_inode->iptr) + iptr_index);
        iptr_index++;
        continue;
      }
      break;
    }
  
    free_inode(desired_direntry->inum);
  }

  memset(desired_direntry, 0, sizeof(direntry));
  // assert that it has been delelted
  assert(desired_direntry->inum == 0);

  // TODO
  // check to see if any more
  if (is_block_empty(curr_pnum)) {
    printf("calling inode shrink from directory delete\n");
    // inode_shrink();
  }

  return 0;
}

// this should only be called when there are no more links/refs, deletes
// direntry in directory with name
int directory_delete(inode* dd, const char* name) {
  // assert(dd->refs == 0);
  printf("called directory delete\n");

  printf("trying to delete: %s\n", name);

  if(strcmp(name, "..") == 0 || strcmp(name, ".") == 0 ) {
    printf("called delete on . or ..");
    return 0;
  }
  int curr_pnum = dd->ptrs[0];
  int iptr_index = -1;
  int* iptr_page = (int*)pages_get_page(dd->iptr);

  // this will run until it finds matching direntry or checks all direntries
  while (find_in_block(curr_pnum, name) < 0) {
    if (iptr_index < 0)
    {
      curr_pnum = dd->ptrs[1];
    }
    else
    {
      curr_pnum = *(iptr_page + iptr_index);
    }

    if (curr_pnum == 0) {
      // have reached end of this directory
      printf(" exiting directory delete: failure\n");
      return -ENOENT;
    }
    iptr_index++;
  }

  int direntry_index = find_in_block(curr_pnum, name);
  direntry* curr_directory = (direntry*)pages_get_page(curr_pnum);
  direntry* desired_direntry = &curr_directory[direntry_index];
  inode* desired_inode = get_inode(desired_direntry->inum);

  int mode = desired_inode->mode & S_IFMT;
  int filemode = S_IFREG;

 
  desired_inode->refs--;

  // assert( desired_inode->refs >= 0);

  printf("target inode refs are now %d\n", desired_inode->refs);

  // if ((desired_inode->mode & S_IFMT) == S_IFREG)
  // {
    return delete_file(desired_direntry, desired_inode, curr_pnum);
  // }
  // else if ((desired_inode->mode & S_IFMT) == S_IFDIR)
  // {
  //   return delete_folder(desired_direntry, desired_inode, curr_pnum);
  // }
  // printf("UNREACHABLE\n\n");
}

// cons each of the names of the direntries at page with the rest
slist* cons_page_contents(int pnum, int starting_index, slist* rest) {
  printf("entered cons page contents, pnum: %ld, si %ld\n", pnum,
         starting_index);
  direntry* block = (direntry*)pages_get_page(pnum);
  slist* contents = rest;
  int ii = starting_index;
  while (ii < MAX_DIRENTRIES) {
    direntry* curr_dirent = &block[ii];
    if (curr_dirent->inum != 0) {
      contents = s_cons(curr_dirent->name, contents);
    }
    ii++;
  }
  printf("exiting cons page\n");
  return contents;
}

// will put all the contents of directory into an slist
slist* directory_list(const char* path) {
  printf("entered directory list with path: %s\n", path);

  char* fname = get_filename_from_path(path);

  int parent_inum = tree_lookup(path);
  inode* dd = get_inode(directory_lookup(get_inode(parent_inum), fname));
  int curr_pnum = dd->ptrs[0];

  // TODO refactor this
  slist* contents = s_cons(".", NULL);
  if (dd->ptrs[0] == 2)
  {
    contents = cons_page_contents(curr_pnum, 1, contents);
  }
  else
  {
    contents = cons_page_contents(curr_pnum, 2, contents);
  }

  int iptr_index = 0;
  int* iptr_page = (int*)pages_get_page(dd->iptr);
  curr_pnum = dd->ptrs[1];

  // this will run until it finds matching direntry or checks all direntries
  while (curr_pnum != 0) {
    contents = cons_page_contents(curr_pnum, 0, contents);
    curr_pnum = *(iptr_page + iptr_index);
    iptr_index++;
  }

  printf("successfully exited directory list\n");
  return s_reverse(contents);
}