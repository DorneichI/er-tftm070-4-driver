/* _fastio.c — fast GPIO access for the ER-TFTM070-4V2.1 (SSD1963)
 * 16-bit 8080 bus via /dev/gpiomem.
 *
 * The display board is strapped for 16-bit 8080: one WR strobe latches
 * one 16-bit pixel (DB0-7 = low byte, DB8-15 = high byte).  Register
 * access uses DB0-7 regardless of bus width.
 *
 * This module is a port of the verified `legacy/fill.c` pixel blaster
 * plus the register-level operations the driver needs.  It is OPTIONAL:
 * if it cannot be built (no compiler on the target), ertftm070 falls
 * back to a pure-Python backend automatically.
 *
 * The 18 bus pins (8 low + 8 high data lines + WR + RD) are passed to
 * open() — ertftm070.pins.Pins validation has already bounded them to
 * the 40-pin header (BCM 0..27); this module accepts 0..31, the bank-0
 * GPIO the BCM2835 register block can strobe.
 *
 * BCM2835-style GPIO register word offsets into the /dev/gpiomem
 * mapping (same offsets on Pi Zero..4; /dev/gpiomem maps the right
 * block on each):
 *   GPFSEL0 = 0x00  -> word 0   (pin function select, 10 pins/word)
 *   GPSET0  = 0x1C  -> word 7   (write 1 = set pin high)
 *   GPCLR0  = 0x28  -> word 10  (write 1 = set pin low)
 *   GPLEV0  = 0x34  -> word 13  (pin levels)
 *
 * Timing: WR strobes use calibrated spin loops (~400 ns per phase on
 * any CPU — see calibrate()).  This reproduces the ~1.6 µs/pixel cycle
 * of the verified legacy/fill.c on a Pi Zero W; faster cycles hit
 * panel-fetch contention (a dropped write word per row), so the
 * verified timing is the target on every model.
 *
 * The GIL is held during pixel_stream(); a full-screen blit therefore
 * blocks the interpreter for ~0.6 s.  Releasing the GIL would mean
 * locking the register state — overkill for a single-display driver.
 *
 * Single-owner: the mapping is module state, so a second open() while
 * one bus is mapped raises RuntimeError (no silent sharing, and closing
 * one bus can never tear a mapping down under another).
 *
 * Nothing touches hardware until open() is called, so importing this
 * module is always safe.
 */
#define PY_SSIZE_T_CLEAN
#include <Python.h>

#include <errno.h>
#include <fcntl.h>
#include <stdint.h>
#include <string.h>
#include <sys/mman.h>
#include <time.h>
#include <unistd.h>

/* Word offsets into the mapped GPIO block (see header). */
#define GPSET0 7
#define GPCLR0 10
#define GPLEV0 13

#define MAP_LEN 0xB4  /* words 0..44; GPLEV0 (word 13) is well inside */
#define GPIO_MAX 32   /* bank-0 only: FSEL words 0..3, GPSET0/CLR0/LEV0 */

/* Bus pins (BCM numbering) — the defaults match ertftm070.pins.DEFAULT_PINS
 * (the hardware-verified wiring); open() may override all 18. */
static int db_low[8] = {4, 17, 27, 22, 5, 6, 13, 19};   /* DB0..DB7 */
static int db_high[8] = {7, 8, 9, 10, 11, 18, 23, 24};  /* DB8..DB15 */
static int wr_pin = 21;
static int rd_pin = 16;

/* Module state.  The bus is opened explicitly — never at import. */
static int bus_fd = -1;
static volatile uint32_t *gpio = NULL;

/* Precomputed masks for the 16 data pins plus the WR/RD strobes. */
static uint32_t clr_low, clr_high, clr_all;
static uint32_t set_low[256], set_high[256];
static uint32_t wr_mask, rd_mask;
static int masks_ready = 0;

/* Spin-loop calibration: iterations per phase.  50 is the verified
 * Pi Zero W value and the default until calibrate() runs. */
static int spin_iter = 50;
static int calibrated = 0;

static void init_masks(void)
{
    int b, i;
    clr_low = clr_high = 0;
    for (b = 0; b < 8; b++) {
        clr_low |= 1u << db_low[b];
        clr_high |= 1u << db_high[b];
    }
    clr_all = clr_low | clr_high;
    for (b = 0; b < 256; b++) {
        set_low[b] = set_high[b] = 0;
        for (i = 0; i < 8; i++) {
            if (b & (1 << i)) {
                set_low[b] |= 1u << db_low[i];
                set_high[b] |= 1u << db_high[i];
            }
        }
    }
    wr_mask = 1u << wr_pin;
    rd_mask = 1u << rd_pin;
    masks_ready = 1;
}

static void spin(void)
{
    volatile int i;
    for (i = 0; i < spin_iter; i++)
        ;
}

/* Measure the cost of one spin-loop iteration and re-scale spin_iter so
 * each phase lasts ~400 ns on this CPU — the ~1.6 µs/pixel cycle of the
 * verified legacy/fill.c on the Pi Zero W.  (Faster cycles showed one
 * dropped write word per row at the x=400 column — panel-fetch
 * contention in the SSD1963's GRAM arbitration.)  Called lazily before
 * the first strobed operation. */
static void calibrate(void)
{
    struct timespec t0, t1;
    volatile long r;
    volatile int i;
    const long rounds = 10000;
    double iter_ns;

    if (calibrated)
        return;
    calibrated = 1;

    clock_gettime(CLOCK_MONOTONIC, &t0);
    for (r = 0; r < 2000; r++)
        for (i = 0; i < rounds; i++)
            ;
    clock_gettime(CLOCK_MONOTONIC, &t1);

    iter_ns = ((t1.tv_sec - t0.tv_sec) * 1e9 + (t1.tv_nsec - t0.tv_nsec))
              / ((double)rounds * 2000.0);
    if (iter_ns <= 0.0)  /* clock too coarse; keep the Zero W default */
        return;
    spin_iter = (int)(400.0 / iter_ns);
    if (spin_iter < 1)
        spin_iter = 1;
}

/* Set pin `pin` (0..31) to output (mode=1) or input (mode=0).
 * GPFSEL word = pin / 10, three bits per pin at (pin % 10) * 3. */
static void pin_mode_raw(int pin, int output)
{
    int word = pin / 10;
    int shift = (pin % 10) * 3;
    uint32_t mask = 7u << shift;
    gpio[word] = (gpio[word] & ~mask) | ((output ? 1u : 0u) << shift);
}

static void pin_write_raw(int pin, int level)
{
    gpio[level ? GPSET0 : GPCLR0] = 1u << pin;
}

/* One register byte on DB0-7 with a WR strobe. */
static void bus_write_byte(uint8_t value)
{
    if (!masks_ready)
        init_masks();
    calibrate();
    gpio[GPCLR0] = clr_low;          /* DB0-7 low */
    gpio[GPSET0] = set_low[value];   /* data valid */
    spin();                          /* data setup */
    gpio[GPCLR0] = wr_mask;          /* WR low: write cycle starts */
    spin();                          /* WR low pulse width */
    gpio[GPSET0] = wr_mask;          /* WR high: byte latched */
    spin();                          /* inter-byte gap */
}

/* Sample all 16 data lines while RD is strobed low.  Returns one word. */
static uint32_t bus_read_word(void)
{
    struct timespec tw = {0, 2000};  /* 2 µs: let the controller drive the bus */
    uint32_t lev, value = 0;
    int b;

    calibrate();
    if (!masks_ready)
        init_masks();  /* rd_mask for the strobe below */
    for (b = 0; b < 16; b++)
        pin_mode_raw(b < 8 ? db_low[b] : db_high[b - 8], 0);  /* inputs */
    gpio[GPCLR0] = rd_mask;          /* RD low: sample window */
    nanosleep(&tw, NULL);
    lev = gpio[GPLEV0];
    gpio[GPSET0] = rd_mask;          /* RD high */
    for (b = 0; b < 16; b++) {
        int pin = b < 8 ? db_low[b] : db_high[b - 8];
        if (lev & (1u << pin))
            value |= 1u << b;
        pin_mode_raw(pin, 1);        /* back to outputs */
    }
    return value;
}

/* ---------------- Python-level wrappers ---------------- */

static PyObject *py_open(PyObject *self, PyObject *args)
{
    int pins[18];  /* 8 low + 8 high + wr + rd */
    int i, j, fd;
    void *map;

    if (gpio) {
        PyErr_SetString(PyExc_RuntimeError,
            "GPIO registers are already mapped — the fast backend is "
            "single-owner (one Display at a time)");
        return NULL;
    }
    if (!PyArg_ParseTuple(args,
                          "(iiiiiiiiiiiiiiiiii):open",
                          &pins[0], &pins[1], &pins[2], &pins[3],
                          &pins[4], &pins[5], &pins[6], &pins[7],
                          &pins[8], &pins[9], &pins[10], &pins[11],
                          &pins[12], &pins[13], &pins[14], &pins[15],
                          &pins[16], &pins[17]))
        return NULL;
    for (i = 0; i < 18; i++) {
        if (pins[i] < 0 || pins[i] >= GPIO_MAX) {
            PyErr_SetString(PyExc_ValueError,
                "bus pins must be BCM GPIO 0..31 (bank-0 registers)");
            return NULL;
        }
        for (j = 0; j < i; j++) {
            if (pins[j] == pins[i]) {
                PyErr_SetString(PyExc_ValueError,
                    "bus pin used twice — the 18 data/WR/RD pins must be "
                    "distinct");
                return NULL;
            }
        }
    }
    fd = open("/dev/gpiomem", O_RDWR | O_SYNC);
    if (fd < 0) {
        PyErr_SetFromErrnoWithFilename(PyExc_OSError, "/dev/gpiomem");
        return NULL;
    }
    map = mmap(NULL, MAP_LEN, PROT_READ | PROT_WRITE, MAP_SHARED, fd, 0);
    if (map == MAP_FAILED) {
        int err = errno;
        close(fd);
        errno = err;
        PyErr_SetFromErrno(PyExc_OSError);
        return NULL;
    }
    bus_fd = fd;
    gpio = (volatile uint32_t *)map;
    /* Commit the wiring only after the mapping succeeded; the pin masks
     * are rebuilt lazily on the next strobe. */
    for (i = 0; i < 8; i++) {
        db_low[i] = pins[i];
        db_high[i] = pins[i + 8];
    }
    wr_pin = pins[16];
    rd_pin = pins[17];
    masks_ready = 0;
    Py_RETURN_NONE;
}

static PyObject *py_close(PyObject *self, PyObject *noargs)
{
    if (gpio) {
        munmap((void *)gpio, MAP_LEN);
        gpio = NULL;
    }
    if (bus_fd >= 0) {
        close(bus_fd);
        bus_fd = -1;
    }
    masks_ready = 0;  /* stale masks must not outlive the mapping */
    Py_RETURN_NONE;
}

static int check_open(void)
{
    if (!gpio) {
        PyErr_SetString(PyExc_RuntimeError, "bus not open — call open() first");
        return -1;
    }
    return 0;
}

static PyObject *py_pin_mode(PyObject *self, PyObject *args)
{
    int pin, output;
    if (!PyArg_ParseTuple(args, "ip:pin_mode", &pin, &output))
        return NULL;
    if (check_open() < 0)
        return NULL;
    if (pin < 0 || pin >= GPIO_MAX) {
        PyErr_SetString(PyExc_ValueError, "pin out of range 0..31 (bank-0 GPIO)");
        return NULL;
    }
    pin_mode_raw(pin, output);
    Py_RETURN_NONE;
}

static PyObject *py_pin_write(PyObject *self, PyObject *args)
{
    int pin, level;
    if (!PyArg_ParseTuple(args, "ip:pin_write", &pin, &level))
        return NULL;
    if (check_open() < 0)
        return NULL;
    if (pin < 0 || pin >= GPIO_MAX) {
        PyErr_SetString(PyExc_ValueError, "pin out of range 0..31 (bank-0 GPIO)");
        return NULL;
    }
    pin_write_raw(pin, level);
    Py_RETURN_NONE;
}

static PyObject *py_write_byte(PyObject *self, PyObject *args)
{
    int value;
    if (!PyArg_ParseTuple(args, "i:write_byte", &value))
        return NULL;
    if (check_open() < 0)
        return NULL;
    if (value < 0 || value > 255) {
        PyErr_SetString(PyExc_ValueError, "byte out of range 0..255");
        return NULL;
    }
    bus_write_byte((uint8_t)value);
    Py_RETURN_NONE;
}

static PyObject *py_read_word(PyObject *self, PyObject *noargs)
{
    if (check_open() < 0)
        return NULL;
    return PyLong_FromLong((long)bus_read_word());
}

/* pixel_stream(buffer): one WR strobe per 16-bit word (bit 0 on DB0),
 * plus the 2 trailing dummy pixels that absorb this chip's burst-tail
 * quirk (the final ~1.5 pixels of a burst are lost when CS releases). */
static PyObject *py_pixel_stream(PyObject *self, PyObject *arg)
{
    Py_buffer view;
    const uint8_t *p;
    Py_ssize_t i, n;
    uint32_t set, last_set = 0;

    if (check_open() < 0)
        return NULL;
    if (PyObject_GetBuffer(arg, &view, PyBUF_CONTIG_RO) < 0)
        return NULL;
    if (view.len % 2) {
        PyBuffer_Release(&view);
        PyErr_SetString(PyExc_ValueError,
                        "buffer length must be even (one 16-bit word per pixel)");
        return NULL;
    }
    if (!masks_ready)
        init_masks();
    calibrate();

    n = view.len / 2;
    p = (const uint8_t *)view.buf;
    for (i = 0; i < n; i++) {
        uint16_t w;
        memcpy(&w, p + i * 2, 2);
        set = set_low[w & 0xFF] | set_high[w >> 8];
        last_set = set;
        gpio[GPCLR0] = clr_all;      /* all 16 data pins low */
        gpio[GPSET0] = set;          /* pixel bits valid */
        spin();
        gpio[GPCLR0] = wr_mask;      /* WR low: write cycle starts */
        spin();
        gpio[GPSET0] = wr_mask;      /* WR high: pixel latched */
        spin();
    }
    /* 2 trailing dummy pixels (same value as the last real pixel,
     * matching the verified legacy/fill.c behaviour). */
    for (i = 0; i < 2; i++) {
        gpio[GPCLR0] = clr_all;
        gpio[GPSET0] = last_set;
        spin();
        gpio[GPCLR0] = wr_mask;
        spin();
        gpio[GPSET0] = wr_mask;
        spin();
    }

    PyBuffer_Release(&view);
    Py_RETURN_NONE;
}

static PyMethodDef methods[] = {
    {"open", py_open, METH_VARARGS,
     "open(pins) — open /dev/gpiomem and map the GPIO registers.  "
     "pins is an 18-tuple: 8 low + 8 high data pins, WR, RD (BCM 0..31).  "
     "Raises RuntimeError if already open (single-owner)."},
    {"close", py_close, METH_NOARGS,
     "close() — unmap the GPIO registers and close the fd.  "
     "Safe to call more than once."},
    {"pin_mode", py_pin_mode, METH_VARARGS,
     "pin_mode(pin, output) — set a pin to output (1) or input (0)."},
    {"pin_write", py_pin_write, METH_VARARGS,
     "pin_write(pin, level) — drive a pin high (1) or low (0)."},
    {"write_byte", py_write_byte, METH_VARARGS,
     "write_byte(value) — one register byte on DB0-7 with a WR strobe."},
    {"read_word", py_read_word, METH_NOARGS,
     "read_word() — sample 16 bits on DB0-15 with an RD strobe."},
    {"pixel_stream", py_pixel_stream, METH_O,
     "pixel_stream(buffer) — one WR strobe per 16-bit word, plus 2 trailing dummies."},
    {NULL, NULL, 0, NULL},
};

static struct PyModuleDef module = {
    PyModuleDef_HEAD_INIT,
    "_fastio",
    "Fast /dev/gpiomem access for the ER-TFTM070-4V2.1 16-bit 8080 bus.  "
    "Internal to ertftm070 — use the ertftm070.Display API instead.",
    -1,
    methods,
};

PyMODINIT_FUNC PyInit__fastio(void)
{
    return PyModule_Create(&module);
}
