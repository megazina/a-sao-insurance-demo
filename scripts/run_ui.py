#!/usr/bin/env python3
"""Start the AussieCare portal (FastAPI + uvicorn)."""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))

import uvicorn

if __name__ == "__main__":
    uvicorn.run(
        "server:app",
        host=os.environ.get("HOST", "127.0.0.1"),
        port=int(os.environ.get("PORT", os.environ.get("CHAINLIT_PORT", "8081"))),
        reload=False,
    )
