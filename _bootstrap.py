"""
Local import bootstrap for the standalone 03_ccf example repository.

Purpose:
    Scripts in this directory live in a separate Git repository from the main
    ``dasQt`` package. This helper finds the sibling/main ``dasQt`` checkout and
    inserts it into ``sys.path`` before importing package APIs.

Usage:
    from _bootstrap import ensure_dasqt_on_path
    ensure_dasqt_on_path(__file__)
"""

from __future__ import annotations

import sys
from pathlib import Path


def ensure_dasqt_on_path(script_file: str | Path) -> Path:
    """Find the main dasQt project root and prepend it to ``sys.path``."""
    script_path = Path(script_file).resolve()
    candidates = [
        script_path.parents[2],
        script_path.parents[1].with_name("dasQt"),
        Path("/Users/zhangzhiyu/MyProjects/dasQt"),
    ]
    for candidate in candidates:
        if (candidate / "dasQt" / "__init__.py").exists():
            candidate_text = str(candidate)
            if candidate_text not in sys.path:
                sys.path.insert(0, candidate_text)
            return candidate
    raise RuntimeError(
        "Cannot locate the main dasQt project. Expected a checkout at "
        "/Users/zhangzhiyu/MyProjects/dasQt or next to dasQt-example."
    )
