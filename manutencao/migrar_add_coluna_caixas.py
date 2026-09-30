"""Migração única: adiciona `fat_invoice_item.caixas` (NUMERIC, nullable).

Suporte à regra de negócio do açougue (item de peso variável, confirmada com
o cliente após o caso Cheney Brothers/05-9100063327): no pedido, o que se
sabe com exatidão é a CAIXA — o peso só é conhecido na conferência, porque
varia por natureza do produto (fresco x congelado, que carrega água a mais).
A conciliação passou a comparar caixa da invoice x `Ordered` do Catapult
para esses itens (`conciliacao/reconcile_erp.py::_comparar_grupo`,
`commons/matcher.py::qty_matches_po_cases`) — precisa de onde gravar essa
contagem, separada do peso que já mora em `qtd`.

`IF NOT EXISTS` — rodar de novo não quebra nada.

    python -m manutencao.migrar_add_coluna_caixas            # dry-run: só mostra o SQL
    python -m manutencao.migrar_add_coluna_caixas --aplicar  # roda a migração
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
    ADD COLUMN IF NOT EXISTS caixas NUMERIC;
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
        print("\nColuna 'caixas' pronta em fat_invoice_item.")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
