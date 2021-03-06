// based on cs3650 starter code

#include "slist.h"

#include <alloca.h>
#include <assert.h>
#include <stdlib.h>
#include <string.h>

char* get_filename_from_path(const char* path) {
  slist* path_list = s_split(path, '/');
  while (path_list->next) {
    path_list = path_list->next;
  }
  return path_list->data;
}

slist* s_cons(const char* text, slist* rest) {
  slist* xs = malloc(sizeof(slist));
  xs->data = strdup(text);
  xs->refs = 1;
  xs->next = rest;
  return xs;
}

void s_free(slist* xs) {
  if (xs == 0) {
    return;
  }

  xs->refs -= 1;

  if (xs->refs == 0) {
    s_free(xs->next);
    free(xs->data);
    free(xs);
  }
}

slist* s_split(const char* text, char delim) {
  if (*text == 0) {
    // assert(1 == -1);
    return 0;
  }

  int plen = 0;
  while (text[plen] != 0 && text[plen] != delim) {
    plen += 1;
  }

  int skip = 0;
  if (text[plen] == delim) {
    skip = 1;
  }

  slist* rest = s_split(text + plen + skip, delim);
  char* part = alloca(plen + 2);
  memcpy(part, text, plen);
  part[plen] = 0;

  return s_cons(part, rest);
}

// Reverses the given slist, then returns it
slist* s_reverse(slist* original) {
  slist* reversed = NULL;
  slist* og = original;

  while (og != NULL) {
    reversed = s_cons(og->data, reversed);
    og = og->next;
  }

  return reversed;
}