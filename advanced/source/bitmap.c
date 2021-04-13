
#include <assert.h>
#include <stdint.h>
#include <stdio.h>

/**
 *
 * Inspired by https://github.com/ryanlayer/cs/tree/master/bitmap
 *
 */

int bitmap_get(void *bm, int ii) {
  assert(ii < 256);

  uint8_t *bitmap = (uint8_t *)bm;

  return ((bitmap[ii / 8]) >> (7 - (ii % 8)) & 1);
}

void bitmap_put(void *bm, int ii, int vv) {
  assert(ii < 256);
  assert(vv == 0 || vv == 1);

  uint8_t *bitmap = (uint8_t *)bm;

  if (vv != bitmap_get(bm, ii)) bitmap[ii / 8] ^= 1 << (7 - (ii % 8));
}

void bitmap_print(void *bm, int size) {
  for (int ii = 0; ii < size; ii++) {
    printf("%d : %d\n", ii, bitmap_get(bm, ii));
  }
}
