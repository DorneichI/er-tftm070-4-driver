# Wiring

The display's 40-pin FPC connects to the Pi's 40-pin header with **24 wires**:
16 data, 5 control, 1 backlight, 1 power, 1 ground.

> The connector pin numbering below is from the ER-TFTM070-4V2.1 datasheet
> (section 4.1) and was verified against hardware. Pin 1 = VSS, pin 40 = VSS.
> If your backlight works but nothing else responds, suspect the cable
> orientation **before** anything else.

## Pin-by-pin

### Data bus (16 bits!)

| Display pin | Signal | Pi GPIO | Pi physical pin | Notes |
|---|---|---|---|---|
| 9  | DB0  | GPIO4  | 7  | low byte |
| 10 | DB1  | GPIO17 | 11 | |
| 11 | DB2  | GPIO27 | 13 | |
| 12 | DB3  | GPIO22 | 15 | |
| 13 | DB4  | GPIO5  | 29 | |
| 14 | DB5  | GPIO6  | 31 | |
| 15 | DB6  | GPIO13 | 33 | |
| 16 | DB7  | GPIO19 | 35 | |
| 17 | DB8  | GPIO7  | 26 | high byte — **often left unconnected in 8-bit guides; required here** |
| 18 | DB9  | GPIO8  | 24 | |
| 19 | DB10 | GPIO9  | 21 | |
| 20 | DB11 | GPIO10 | 19 | |
| 21 | DB12 | GPIO11 | 23 | |
| 22 | DB13 | GPIO18 | 12 | |
| 23 | DB14 | GPIO23 | 16 | |
| 24 | DB15 | GPIO24 | 18 | |
| 25–32 | DB16–DB23 | — | — | not needed for 565 color |

### Control, backlight, power

| Display pin | Signal | Pi GPIO | Pi physical pin | Notes |
|---|---|---|---|---|
| 1  | VSS (GND) | — | 6  | ground |
| 2  | VDD (5 V) | — | 2  | see power note below |
| 3  | /CS       | GPIO26 | 37 | active low |
| 4  | D/C       | GPIO20 | 38 | data/command select |
| 5  | E_/RD     | GPIO16 | 36 | tie/drive high for write-only |
| 6  | R/W_/WR   | GPIO21 | 40 | active-low write strobe |
| 7  | RESET     | GPIO12 | 32 | active low |
| 8  | TE        | — | — | tearing effect output, unused |
| 33–38 | touch   | — | — | unused |
| 39 | BL ON/OFF | GPIO25 | 22 | high = backlight on (board J4 bridged) |
| 40 | VSS (GND) | — | — | optional second ground |

## ASCII overview

```
                     Raspberry Pi Zero W                     ER-TFTM070-4V2.1 (40-pin FPC)
                  ┌───────────────────────┐                ┌──────────────────────────┐
                  │                       │                │ 1  VSS ───────── GND     │
   3.3V   1       │  ·  ·  ·  ·  ·  ·  ·  │                │ 2  VDD ───────── 5V      │
   5V     2 ──────│────────────────────────────────────────▶ VDD                     │
                  │                       │                │ 3  /CS ◀─────── GPIO26   │
                  │                       │                │ 4  D/C ◀─────── GPIO20   │
                  │                       │                │ 5  E_/RD ◀────── GPIO16  │
                  │                       │                │ 6  R/W_/WR ◀───── GPIO21 │
                  │                       │                │ 7  RESET ◀────── GPIO12  │
                  │                       │                │ 8  TE        (unused)    │
                  │                       │                │ 9  DB0 ◀─────── GPIO4   │
                  │                       │                │ 10 DB1 ◀─────── GPIO17  │
                  │                       │                │ 11 DB2 ◀─────── GPIO27  │
                  │                       │                │ 12 DB3 ◀─────── GPIO22  │
                  │                       │                │ 13 DB4 ◀─────── GPIO5   │
                  │                       │                │ 14 DB5 ◀─────── GPIO6   │
                  │                       │                │ 15 DB6 ◀─────── GPIO13  │
                  │                       │                │ 16 DB7 ◀─────── GPIO19  │
                  │                       │                │ 17 DB8 ◀─────── GPIO7   │
                  │                       │                │ 18 DB9 ◀─────── GPIO8   │
                  │                       │                │ 19 DB10 ◀────── GPIO9   │
                  │                       │                │ 20 DB11 ◀────── GPIO10  │
                  │                       │                │ 21 DB12 ◀────── GPIO11  │
                  │                       │                │ 22 DB13 ◀────── GPIO18  │
                  │                       │                │ 23 DB14 ◀────── GPIO23  │
                  │                       │                │ 24 DB15 ◀────── GPIO24  │
                  │                       │                │ 25-32 DB16-23 (unused)  │
                  │                       │                │ 33-38 touch    (unused) │
                  │                       │                │ 39 BL ON/OFF ◀── GPIO25 │
    GND   6 ──────│────────────────────────────────────────▶ 40 VSS                   │
                  └───────────────────────┘                └──────────────────────────┘
```

A prettier SVG version lives in [`../diagrams/wiring.svg`](../diagrams/wiring.svg).

## Board configuration

The display's jumpers (as shipped / as verified working):

| Jumper | State | Meaning |
|---|---|---|
| R3 | soldered, R4 open | **8080** interface (not 6800) |
| J10 | open | VDD = **5 V** |
| J3 | open, J4 bridged | backlight controlled by **external** pin |
| J11 | bridged | undocumented in the V2.1 datasheet — irrelevant in 16-bit mode |

## Electrical safety notes

- The board's logic supply (VDDIO) is **3.3 V** even with 5 V VDD
  (the SSD1963 can't take 5 V logic). Pi GPIO levels are safe and meet
  VIH (min 0.8 × VDDIO = 2.64 V).
- The display drives its outputs at 3.3 V too — read-back operations
  cannot damage the Pi.
- **Power**: VDD draws up to 300 mA at 5 V. The Pi's 5 V pin works in
  steady state with a decent PSU, but the inrush when plugging in the
  display once brown-out reset the Pi. For a permanent setup, feed the
  display from its own regulated 5 V ≥ 1 A supply, common ground.
