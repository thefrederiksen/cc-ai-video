"""Allow `python -m ccvideo` as well as the `ccvideo` console script.

The console script is created by pip and lands in a Scripts directory that is not always on
PATH - a per-user install on Windows, or an already-running shell that has not picked up a
PATH change. That is a real trap: the first instruction anyone follows is "run ccvideo
--help", and when it fails the reasonable conclusion is that the install is broken.

This module makes the package runnable through the interpreter that installed it, which
always works. It exists for that reason and does nothing else.
"""

import sys

from .cli import main

if __name__ == "__main__":
    sys.exit(main())
