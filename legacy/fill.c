/* fill.c - pixel fill for SSD1963 16-bit 8080 bus via /dev/gpiomem
 *
 * Usage: fill <color565_hex> <width> <height> [<color> <w> <h> ...]
 *
 * The display is strapped for 16-bit 8080: ONE WR cycle = ONE pixel,
 * DB0-7 carry the low byte, DB8-15 the high byte.  CS low and DC high
 * are managed by the caller.  All runs happen in ONE continuous burst
 * (separate processes lose bytes at the boundaries).
 *
 * BCM2835 GPIO register offsets (via /dev/gpiomem, base = peripheral):
 *   GPSET0 = 0x1C  -> word 7
 *   GPCLR0 = 0x28  -> word 10
 *
 * Pin map (BCM):
 *   DB0..DB7  (low byte)  = 4,17,27,22,5,6,13,19
 *   DB8..DB15 (high byte) = 7,8,9,10,11,18,23,24
 *   WR = 21
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <fcntl.h>
#include <sys/mman.h>
#include <unistd.h>
#include <stdint.h>

static const int db[16] = {4, 17, 27, 22, 5, 6, 13, 19,   /* DB0..DB7 */
                           7, 8, 9, 10, 11, 18, 23, 24};  /* DB8..DB15 */
#define WR_PIN 21
#define GPSET0 7
#define GPCLR0 10

static void spin(void)
{
    volatile int i;
    for (i = 0; i < 50; i++)
        ;
}

static void put_pixel(volatile uint32_t *gpio, uint32_t clr, uint32_t set)
{
    gpio[GPCLR0] = clr;           /* all 16 data pins low (WR stays high) */
    gpio[GPSET0] = set;           /* pixel bits valid */
    spin();                       /* data setup */
    gpio[GPCLR0] = (1u << WR_PIN);/* WR low -> write cycle starts */
    spin();                       /* WR low pulse width */
    gpio[GPSET0] = (1u << WR_PIN);/* WR high -> pixel latched */
    spin();                      /* inter-byte gap (no syscall) */
}

int main(int argc, char **argv)
{
    if (argc < 4 || (argc - 1) % 3 != 0) {
        fprintf(stderr, "usage: %s <color565_hex> <width> <height> [<color> <w> <h> ...]\n", argv[0]);
        return 1;
    }
    int runs = (argc - 1) / 3;

    int fd = open("/dev/gpiomem", O_RDWR | O_SYNC);
    if (fd < 0) { perror("open /dev/gpiomem"); return 1; }
    volatile uint32_t *gpio = mmap(0, 0xB4, PROT_READ | PROT_WRITE,
                                   MAP_SHARED, fd, 0);
    if (gpio == MAP_FAILED) { perror("mmap"); return 1; }
    close(fd);

    uint32_t clr = 0;
    for (int b = 0; b < 16; b++)
        clr |= (1u << db[b]);

    for (int r = 0; r < runs; r++) {
        unsigned color = (unsigned)strtoul(argv[1 + r * 3], NULL, 0);
        int w = atoi(argv[2 + r * 3]);
        int h = atoi(argv[3 + r * 3]);
        uint32_t set = 0;
        for (int b = 0; b < 16; b++)
            if (color & (1u << b))
                set |= (1u << db[b]);
        long total = (long)w * h;
        for (long p = 0; p < total; p++)
            put_pixel(gpio, clr, set);
        /* 2 trailing pixels - absorb the burst-tail quirk */
        put_pixel(gpio, clr, set);
        put_pixel(gpio, clr, set);
    }
    return 0;
}
