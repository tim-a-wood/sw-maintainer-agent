"""Allow `python -m maintain`, which the Windows launcher uses."""

import sys

from .maintain import main

sys.exit(main())
