"""测试引导：开发根目录进 sys.path，``import qwpad`` 走真实包语义。"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HERMES_HOME = Path(os.environ.get("HERMES_HOME", Path.home() / ".hermes"))
HERMES_TREE = Path(os.environ.get("HERMES_TREE", HERMES_HOME / "hermes-agent"))

for path in (ROOT, HERMES_TREE):
    if path.is_dir() and str(path) not in sys.path:
        sys.path.insert(0, str(path))
