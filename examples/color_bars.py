"""Draw the 8 test color bars.  Run: python3 examples/color_bars.py"""
from ertftm070 import Display

BARS = [
    ("black", 0x0000),
    ("red", 0xF800),
    ("green", 0x07E0),
    ("blue", 0x001F),
    ("white", 0xFFFF),
    ("yellow", 0xFFE0),
    ("cyan", 0x07FF),
    ("magenta", 0xF81F),
]

with Display() as lcd:
    w = lcd.width // len(BARS)
    for i, (_name, color) in enumerate(BARS):
        lcd.fill_rect(i * w, 0, w, lcd.height, color)
    print("Expected left->right: {}".format(", ".join(n for n, _ in BARS)))
