// .bit layout (big-endian lengths):
//   u16 n, n bytes of preamble (0F F0 0F F0 0F F0 0F F0 00), u16 = 1,
//   then keyed fields: 'a'..'d' = u16 len + NUL-terminated string, 'e' = u32 len + data.
#include "bitfile.h"

#include <string.h>

static int rd16(const uint8_t *b, size_t len, size_t *p, uint32_t *v)
{
    if (*p + 2 > len) return -1;
    *v = ((uint32_t)b[*p] << 8) | b[*p + 1];
    *p += 2;
    return 0;
}

static int rd32(const uint8_t *b, size_t len, size_t *p, uint32_t *v)
{
    if (*p + 4 > len) return -1;
    *v = ((uint32_t)b[*p] << 24) | ((uint32_t)b[*p + 1] << 16) | ((uint32_t)b[*p + 2] << 8) | b[*p + 3];
    *p += 4;
    return 0;
}

static void copy_field(char *dst, size_t cap, const uint8_t *src, uint32_t n)
{
    size_t k = n < cap - 1 ? n : cap - 1;
    memcpy(dst, src, k);
    dst[k] = 0;   // fields carry their own NUL; this also bounds a malformed one
}

int bitfile_parse(const uint8_t *b, size_t len, bitfile_info_t *out)
{
    memset(out, 0, sizeof(*out));
    size_t p = 0;
    uint32_t n;
    if (rd16(b, len, &p, &n) || n > 64 || p + n > len) return -1;
    p += n;
    if (rd16(b, len, &p, &n) || n != 1) return -2;

    for (int guard = 0; guard < 8; guard++) {
        if (p >= len) return -3;
        uint8_t key = b[p++];
        if (key == 'e') {
            if (rd32(b, len, &p, &n)) return -4;
            out->data_offset = (uint32_t)p;
            out->data_len = n;
            return out->part[0] ? 0 : -5;   // a header without a part name is not one we trust
        }
        if (key < 'a' || key > 'd') return -6;
        if (rd16(b, len, &p, &n) || p + n > len) return -7;
        switch (key) {
        case 'a': copy_field(out->design, sizeof(out->design), b + p, n); break;
        case 'b': copy_field(out->part, sizeof(out->part), b + p, n); break;
        case 'c': copy_field(out->date, sizeof(out->date), b + p, n); break;
        case 'd': copy_field(out->time, sizeof(out->time), b + p, n); break;
        }
        p += n;
    }
    return -8;
}
