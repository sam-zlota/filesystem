// based on cs3650 starter code

#ifndef UTIL_H
#define UTIL_H

#include <string.h>

#include "slist.h"

static int MODE_FILE = 0100644;
static int MODE_DIR = 0040000;

static int streq(const char* aa, const char* bb) { return strcmp(aa, bb) == 0; }

static int min(int x, int y) { return (x < y) ? x : y; }

static int max(int x, int y) { return (x > y) ? x : y; }

static int clamp(int x, int v0, int v1) { return max(v0, min(x, v1)); }

static int bytes_to_pages(int bytes) {
  int quo = bytes / 4096;
  int rem = bytes % 4096;
  if (rem == 0) {
    return quo;
  } else {
    return quo + 1;
  }
}

static void join_to_path(char* buf, char* item) {
  int nn = strlen(buf);
  if (buf[nn - 1] != '/') {
    strcat(buf, "/");
  }
  strcat(buf, item);
}

static char* get_filename_from_path(const char* path) {
  slist* path_list = s_split(path, '/');
  while (path_list->next) {
    path_list = path_list->next;
  }
  return path_list->data;
}

#endif
