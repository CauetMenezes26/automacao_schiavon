"""Migração única: adiciona `fat_invoice_item.codigo_manual` (VARCHAR, nullable).

Suporte ao casamento por código escrito à mão — achado real contra o Catapult
(Cheney Brothers, invoice 05-9100063327): o código IMPRESSO na nota é do
catálogo do fornecedor e não bate com o Catapult, mas o número anotado à mão
na linha é o scancode/Supplier Unit ID de verdade. `match_items_po`
(`commons/matcher.py`) passou a tentar esse código como último candidato,
depois de `item_code`/`upc` — precisa de onde gravar essa contagem, separada
das anotações de texto livre que já moram em `anotacao_manual`.

`IF NOT EXISTS` — rodar de novo não quebra nada.

    python -m manutencao.migrar_add_coluna_codigo_manual            # dry-run
    python -m manutencao.migrar_add_coluna_codigo_manual --aplicar  # roda a migração
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from commons.db import connect_db  # noqa: E402
from domain.config import carregar_config  # noqa: E402
from domain.service.processo_service import SCHEMA  # noqa: E402

_SQL = f"""
ALTER TABLE {SCHEMA}.fat_invoice_item
    ADD COLUMN IF NOT EXISTS codigo_manual VARCHAR(40);
"""


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--aplicar", action="store_true", help="Executa a migração (default: dry-run)")
    args = parser.parse_args()

    print(_SQL.strip())
    if not args.aplicar:
        print("\nDry-run — nada foi executado. Rode com --aplicar para aplicar.")
        return

    config = carregar_config()
    conn = connect_db(config.banco)
    try:
        with conn.cursor() as cur:
            cur.execute(_SQL)
        conn.commit()
        print("\nColuna 'codigo_manual' pronta em fat_invoice_item.")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
