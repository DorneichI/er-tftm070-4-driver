# Security Policy

## Supported versions

The latest release published to PyPI is the only supported version.
Issues are fixed forward; there are no backports.

## Reporting a vulnerability

Please use GitHub's private vulnerability reporting so a fix can land
before the issue is public:

<https://github.com/DorneichI/er-tftm070-4-driver/security/advisories/new>

In scope: anything a user can trigger through the package — memory
safety in the C extension (`src/ertftm070/_fastio.c`), buffer handling
in the Python backends, and paths where untrusted input (image files,
CLI arguments) reaches the bus.

Out of scope: issues that require physical access to the device's GPIO
pins — no software can prevent those.

There is no response SLA. Reports are handled on a best-effort basis,
whenever the human gets to them — which may be a while. Do not count on
a prompt fix.
