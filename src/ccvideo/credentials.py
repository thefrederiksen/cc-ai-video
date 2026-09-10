"""Read one API key, at the moment it is used.

A key is never stored in this library, never written to an output, and never printed. It is
read from the environment first, then from a `.env`-shaped file whose location is given by
CCVIDEO_CREDENTIALS (or the caller). Nothing here has a default key, a bundled key, or a
"try without one" path: a missing key stops the run and says exactly what to set.
"""

import os
from pathlib import Path

ENV_VAR = "CCVIDEO_CREDENTIALS"


def _credentials_file(path=None):
    if path:
        return Path(path)
    configured = os.environ.get(ENV_VAR, "").strip()
    if configured:
        return Path(configured)
    return Path.home() / ".config" / "ccvideo" / "credentials.env"


def get(name, path=None):
    """The value of `name`, from the environment or the credentials file. Never returns empty."""
    value = os.environ.get(name, "").strip()
    if value:
        return _checked(name, value, "the environment")

    creds = _credentials_file(path)
    if not creds.exists():
        raise SystemExit(
            "%s is not set and there is no credentials file at %s.\n"
            "Set the environment variable, or point %s at a file holding a line %s=..."
            % (name, creds, ENV_VAR, name)
        )
    for line in creds.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = line.strip()
        if line.startswith("#") or "=" not in line:
            continue
        key, _, raw = line.partition("=")
        if key.strip() == name:
            return _checked(name, raw.strip().strip('"').strip("'"), str(creds))
    raise SystemExit("%s is not in %s. Add a line %s=..." % (name, creds, name))


def _checked(name, value, where):
    if len(value) < 20 or value.startswith("sk-...") or value.lower() in ("changeme", "todo"):
        raise SystemExit(
            "%s in %s is a placeholder, not a key. Nothing is synthesised without a real one."
            % (name, where)
        )
    return value
