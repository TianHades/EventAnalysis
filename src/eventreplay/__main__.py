"""`python -m eventreplay` 入口：与 console script 同源（eventreplay = eventreplay.cli:app）。"""

from __future__ import annotations

from .cli import app

if __name__ == "__main__":
    app()
