// based on cs3650 starter code

#ifndef SLIST_H
#define SLIST_H

typedef struct slist {
  char* data;
  int refs;
  struct slist* next;
} slist;

slist* s_cons(const char* text, slist* rest);
void s_free(slist* xs);
slist* s_split(const char* text, char delim);
slist* s_reverse(slist* original);

char *get_filename_from_path(const char *path);
#endif
