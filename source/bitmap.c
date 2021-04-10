
#include <stdint.h>
#include <stdio.h>
#include <assert.h>

// int mask(int rem) {
//   assert(rem < 8);
//   // rem, 0 ==> 1000_0000 ==> 128
//   // rem, 1 ==> 0100_0000 ==> 64
//   //...
//   // rem 6 ==> 0000_0010 ==> 2
//   // rem 7 ==> 0000_0001 ==> 1
//   int exp = 8 - rem + 1;
//   int res = 1;
//   while (exp) {
//     res *= 2;
//     exp--;
//   }
//   return res;
// }


// rem, 0 ==> 1000_0000 ==> 128
// rem, 1 ==> 0100_0000 ==> 64
// ...
// rem 6 ==> 0000_0010 ==> 2
// rem 7 ==> 0000_0001 ==> 1
static int mask[8] = {128, 64, 32, 16, 8, 4, 2, 1};


int bitmap_get(void *bm, int ii) {
  assert(ii < 256);
  // int remainder = ii % 8;
  // int bytes = ii / 8;
  uint8_t* bitmap = (uint8_t *)bm;
  // printf("got val: %ld from index: %ld\n", (val & mask[remainder]) , ii);

  return ((bitmap[ii/8]) >> (7 - (ii%8)) & 1);
  // if ((val & mask[remainder]) > 0) {
  //   return 1;
  // }
  // else
  //   return 0;
  // return (val & mask[remainder]) >> remainder;
}

void bitmap_put(void *bm, int ii, int vv) {
  assert(ii < 256);
  assert(vv == 0 || vv == 1);
  // int remainder = ii % 8;
  // int bytes = ii / 8;
  uint8_t *bitmap = (uint8_t *)bm;
  // printf("putting val: %ld into index: %ld\n", vv, ii);
  // printf("val before: %ld\n", *val >> (7 - remainder));

  if(vv != bitmap_get(bm, ii)) 
    bitmap[ii/8] ^= 1 << (7 - (ii%8));
  
  // if (vv ) {
  //   // printf("assinging value: %ld", *val | mask[remainder] >> (7 - remainder));
  //   *val = *val | mask[remainder] << (7 - remainder);
  // } 
  // // else {
  // //   // printf("assinging value: %ld", *val & mask[remainder]);
  // //   *val = *val & mask[remainder] << (7 - remainder);
  // // }
  // // printf("val after: %ld\n", *val);

}

void bitmap_print(void *bm, int size) {
  for (int ii = 0; ii < size; ii++) {
    printf("%d : %d\n", ii, bitmap_get(bm, ii));
  }
}
