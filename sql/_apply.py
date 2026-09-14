"""Aplica um .sql no dwschiavon2, dentro de UMA transação.

    .venv/Scripts/python.exe sql/_apply.py [arquivo.sql]

Sem argumento usa `sql/migrate_dwschiavon2_atual.sql`. Rollback em qualquer
erro. Idempotente se o .sql for.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from commons.db import connect_db, load_env  # noqa: E402
from commons.paths import ENV_PATH  # noqa: E402


def main() -> int:
    alvo = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("sql/migrate_dwschiavon2_atual.sql")
    sql = alvo.read_text(encoding="utf-8")

    conn = connect_db(load_env(ENV_PATH))
    conn.autocommit = False
    cur = conn.cursor()
    try:
        cur.execute(sql)
        for n in conn.notices:
            print("  NOTICE:", n.strip())
        conn.commit()
        print(f"\nCOMMIT -- {alvo.name} aplicado.")
    except Exception as exc:  # noqa: BLE001
        conn.rollback()
        print(f"\nROLLBACK -- nada alterado. Erro: {exc!r}")
        return 1
    finally:
        conn.close()

    conn = connect_db(load_env(ENV_PATH))
    cur = conn.cursor()
    cur.execute("""
        SELECT table_type, table_name
          FROM information_schema.tables
         WHERE table_schema = 'dwschiavon2'
         ORDER BY table_type, table_name
    """)
    linhas = cur.fetchall()
    tabelas = [n for t, n in linhas if t == "BASE TABLE"]
    views = [n for t, n in linhas if t == "VIEW"]
    print(f"\ndwschiavon2: {len(tabelas)} tabelas, {len(views)} view(s)")
    for n in ("agendamento", "dim_sistema", "alerta"):
        print(f"  [{'OK' if n in tabelas else 'FALTA'}] {n}")
    cur.execute("SELECT count(*) FROM dwschiavon2.dim_sistema")
    print(f"  dim_sistema: {cur.fetchone()[0]} linha(s)")
    cur.execute("SELECT count(*) FROM dwschiavon2.agendamento")
    print(f"  agendamento: {cur.fetchone()[0]} linha(s)")
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
