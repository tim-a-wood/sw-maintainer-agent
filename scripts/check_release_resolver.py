"""Check the installer's release resolver against GitHub itself.

The unit tests feed it a canned listing. This runs the real path a
person takes when no reference is pinned, so a change to GitHub's
output, or to git, is caught before an install is.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from install_maintain import newest_release_tag  # noqa: E402


def main() -> int:
    tag = newest_release_tag()
    print(f"Resolved release: {tag or '(none)'}")
    if not re.fullmatch(r"v\d+(\.\d+)*", tag or ""):
        print(f"The resolver returned {tag!r}, which is not a release tag.")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
