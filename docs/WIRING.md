# Wiring

One table, 24 wires, 16-bit 8080. Connector pin numbers are from the
ER-TFTM070-4V2.1 datasheet (section 4.1) and were verified on hardware.

## The wiring table

| Display pin | Signal | Pi GPIO | Pi phys pin | Purpose |
|---|---|---|---|---|
| 1  | VSS    | —        | 6   | ground |
| 2  | VDD    | —        | 2   | 5 V supply (see power notes) |
| 3  | /CS    | GPIO26   | 37  | chip select, active low |
| 4  | D/C    | GPIO20   | 38  | data/command select |
| 5  | E_/RD  | GPIO16   | 36  | read strobe (idle high; write-only here) |
| 6  | R/W_/WR| GPIO21   | 40  | write strobe, active low |
| 7  | RESET  | GPIO12   | 32  | master reset, active low |
| 8  | TE     | —        | —   | tearing effect output, unused |
| 9  | DB0    | GPIO4    | 7   | data, low byte bit 0 |
| 10 | DB1    | GPIO17   | 11  | data, low byte bit 1 |
| 11 | DB2    | GPIO27   | 13  | data, low byte bit 2 |
| 12 | DB3    | GPIO22   | 15  | data, low byte bit 3 |
| 13 | DB4    | GPIO5    | 29  | data, low byte bit 4 |
| 14 | DB5    | GPIO6    | 31  | data, low byte bit 5 |
| 15 | DB6    | GPIO13   | 33  | data, low byte bit 6 |
| 16 | DB7    | GPIO19   | 35  | data, low byte bit 7 |
| 17 | DB8    | GPIO7    | 26  | data, **high byte bit 0** |
| 18 | DB9    | GPIO8    | 24  | data, high byte bit 1 |
| 19 | DB10   | GPIO9    | 21  | data, high byte bit 2 |
| 20 | DB11   | GPIO10   | 19  | data, high byte bit 3 |
| 21 | DB12   | GPIO11   | 23  | data, high byte bit 4 |
| 22 | DB13   | GPIO18   | 12  | data, high byte bit 5 |
| 23 | DB14   | GPIO23   | 16  | data, high byte bit 6 |
| 24 | DB15   | GPIO24   | 18  | data, high byte bit 7 |
| 25–32 | DB16–DB23 | —   | —   | unused (565 needs 16 bits) |
| 33–38 | touch  | —        | —   | unused |
| 39 | BL ON/OFF | GPIO25 | 22  | backlight: high = on |
| 40 | VSS    | —        | —   | optional second ground |

**The 16-bit part matters:** DB8–DB15 are not optional. This board is
strapped for 16-bit 8080; one WR cycle writes one 16-bit pixel with the
low byte on DB0–7 and the high byte on DB8–15. (R3/R4 only select
8080-vs-6800 — they say nothing about bus width. See [LESSONS.md](LESSONS.md).)

## Board configuration

| Jumper | State | Meaning |
|---|---|---|
| R3 | soldered, R4 open | **8080** interface (not 6800) |
| J10 | open | VDD = **5 V** |
| J3 | open, J4 bridged | backlight controlled by **external** pin |
| J11 | bridged | undocumented in the V2.1 datasheet — irrelevant in 16-bit mode |

## Electrical safety notes

- The board's logic supply (VDDIO) is **3.3 V** even with 5 V VDD (the
  SSD1963 can't take 5 V logic). Pi GPIO levels are safe and meet VIH
  (min 0.8 × VDDIO = 2.64 V). Display outputs are 3.3 V too, so read-back
  operations cannot damage the Pi.
- **Power**: VDD draws up to 300 mA at 5 V. The Pi's 5 V pin works in
  steady state with a decent PSU, but the inrush when plugging in the
  display once brown-out reset the Pi. For a permanent setup, feed the
  display from its own regulated 5 V ≥ 1 A supply, common ground.
- If the backlight works but nothing else responds, suspect the FPC
  cable orientation before anything else.
