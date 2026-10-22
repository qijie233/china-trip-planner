"""让测试能直接 import scripts/ 下的两个模块。"""
from __future__ import annotations

import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import fixtures  # noqa: E402,F401  # 顺手把 fixtures 模块暴露给测试
