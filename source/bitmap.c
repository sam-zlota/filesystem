
#include <stdint.h>

int pow(int exp) {

    int res = 1;
    while(exp) {
        res*=2;
        exp--;
    }
    return res;

    
}
int bitmap_get(void* bm, int ii) {
    int remainder = ii % 8; 
    int bytes = ii / 8;
    uint8_t val = *(uint8_t*)(bm + bytes);
    return val & pow(remainder);

}

void bitmap_put(void* bm, int ii, int vv) {
    int remainder = ii % 8; 
    int bytes = ii / 8;
    uint8_t* val = (uint8_t*)(bm + bytes);
    if(vv) 
        *val = *val | pow(remainder);
    else
        *val = *val & pow(remainder);
}


void bitmap_print(void* bm, int size) {

    for(int ii = 0; ii < size; ii++ ) {

        printf("%d : %d\n", ii, bitmap_get(bm, ii));
    }

}

