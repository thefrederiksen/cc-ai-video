"""Read one API key, at the moment it is used.

A key is never stored in this library, never written to an output, and never printed. It is
read from the environment and nowhere else - no file is parsed. Supply it however your machine
keeps secrets (a secrets manager that runs the command with the key set, a CI secret, a shell
export). Nothing here has a default key, a bundled key, or a "try without one" path: a missing
key stops the run and says exactly what to set.
"""

import os


def get(name):
    """The value of `name`, from the environment. Never returns empty."""
    value = os.environ.get(name, "").strip()
    if not value:
        raise SystemExit(
            "%s is not set. Set it in the environment of the ccvideo command - for example run "
            "ccvideo through your secrets manager so it supplies %s." % (name, name)
        )
    return _checked(name, value)


def _checked(name, value):
    if len(value) < 20 or value.startswith("sk-...") or value.lower() in ("changeme", "todo"):
        raise SystemExit(
            "%s in the environment is a placeholder, not a key. Nothing is synthesised without a "
            "real one." % name
        )
    return value
