"""Exception hierarchy for ertftm070."""


class Ertftm070Error(Exception):
    """Base class for all errors raised by ertftm070."""


class SSD1963Error(Ertftm070Error):
    """The display controller answered incorrectly (failed self-test etc.)."""


class NotOnRaspberryPi(Ertftm070Error):
    """No usable /dev/gpiomem — not running on a supported Raspberry Pi.

    The bus backends talk to the BCM2835-style GPIO register block
    exposed by /dev/gpiomem: Raspberry Pi Zero / 1 / 2 / 3 / 4.
    Pi 5 (RP1) uses a different GPIO controller and is not supported yet.
    On any other machine the package imports fine, but no display can be
    opened.
    """
