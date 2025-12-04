#!/usr/bin/env python3
"""
Run DB migration to add the 'pode_fazer_devolucao' column to the 'usuarios' table
in the nelson2 database.

It executes scripts/add_pode_fazer_devolucao_column.sql against DATABASE_URL
using SQLAlchemy async engine (asyncpg), following the same pattern as other
migration helpers in this project.

Usage (local or on Railway shell):

  python scripts/run_migration_pode_fazer_devolucao.py
"""
import asyncio
import os
import sys
from pathlib import Path

from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy import text

# Ensure project root is on sys.path so 'app' package can be imported when run from scripts/
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Load settings from app.core.config (keeps parity with app). Fallback to env var.
try:
    from app.core.config import settings  # type: ignore
    SETTINGS_OK = True
except Exception:
    settings = None  # type: ignore
    SETTINGS_OK = False

SQL_FILE = Path(__file__).with_name("add_pode_fazer_devolucao_column.sql")


async def run() -> None:
    if not SQL_FILE.exists():
        raise FileNotFoundError(f"Arquivo SQL não encontrado: {SQL_FILE}")

    # Ensure async URL (settings already tries to coerce to asyncpg). Fallback to env.
    if SETTINGS_OK and getattr(settings, "DATABASE_URL", None):
        db_url = settings.DATABASE_URL
    else:
        db_url = os.getenv("DATABASE_URL", "")
        if not db_url:
            raise RuntimeError(
                "DATABASE_URL não definido e não foi possível importar app.core.config.settings"
            )

    if not db_url.startswith("postgresql+asyncpg://"):
        if db_url.startswith("postgresql://"):
            db_url = db_url.replace("postgresql://", "postgresql+asyncpg://")

    print("\n=== MIGRATION: add pode_fazer_devolucao column to usuarios ===")
    print(f"DB URL: {db_url}")

    engine = create_async_engine(db_url, echo=False, pool_pre_ping=True)
    try:
        sql = SQL_FILE.read_text(encoding="utf-8")
        # Split on semicolons to avoid multi-statement issues
        statements = [s.strip() for s in sql.split(";") if s.strip()]

        async with engine.begin() as conn:
            for stmt in statements:
                print(f"-> Executando: {stmt[:80]}...")
                await conn.execute(text(stmt))
        print("OK: Migração concluída com sucesso.")
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(run())
