"""Ожидание доступности PostgreSQL (для CI и локальных скриптов)."""
from __future__ import annotations

import os
import sys
import time

from sqlalchemy import create_engine, text


def main() -> int:
    url = os.getenv("DATABASE_URL")
    if not url:
        print("DATABASE_URL is not set", file=sys.stderr)
        return 1
    deadline = time.monotonic() + 90
    last_exc: Exception | None = None
    while time.monotonic() < deadline:
        try:
            eng = create_engine(url, pool_pre_ping=True)
            with eng.connect() as conn:
                conn.execute(text("SELECT 1"))
            return 0
        except Exception as exc:
            last_exc = exc
            time.sleep(2)
    print(f"PostgreSQL not ready: {last_exc}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
