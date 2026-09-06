"""Sleep the panel, then wake it with a finger tap.

The display goes dark (display off + backlight off) and the Pi waits
on the touch chip — with the INT wire it waits on the pin, without it
it polls TD_STATUS.  A tap brings the picture back.

Run: python3 examples/wake_on_touch.py
Needs: the touch wires (docs/WIRING.md), I2C enabled.
"""
import time

from ertftm070 import Display
from ertftm070.touch import Touch

with Display() as lcd, Touch(lcd.bus) as touch:
    lcd.fill(0x07E0)  # green: easy to see coming back
    print("panel will sleep in 3 s — tap the panel to wake it (Ctrl+C exits)")
    time.sleep(3)
    lcd.sleep()  # display off + 0x10 (see the sleep() docstring)
    lcd.backlight(False)
    print("sleeping — waiting for a touch...")

    touch.wait_touch()  # blocks until a finger is on the panel

    lcd.backlight(True)
    lcd.wake()
    print("woke on touch!")
    time.sleep(3)
print("done (display closed, backlight off)")
