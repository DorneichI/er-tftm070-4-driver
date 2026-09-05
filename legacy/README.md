# Legacy bring-up scripts (verified baseline)

These two files drove the display during the hardware bring-up and were
**verified working on the ER-TFTM070-4V2.1** (Raspberry Pi Zero W,
September 2026). They are kept byte-for-byte as the reference baseline
the new package is verified against.

| File | What it is |
|---|---|
| `display_test.py` | RPi.GPIO-based init + self-test + test patterns (slow: ~460 px/s) |
| `fill.c` | C pixel-blaster via /dev/gpiomem (~640k px/s); `gcc -O2 -o fill fill.c` |

**New projects should use the packaged driver instead:**

```bash
pip install ertftm070[Pillow]
```

```python
from ertftm070 import Display

with Display() as lcd:
    lcd.fill(0xF800)
```

See the [README](../README.md) and [docs/](../docs/).
