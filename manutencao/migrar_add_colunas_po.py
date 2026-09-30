"""Migração única: adiciona a `fat_conciliacao_item` o lado PO da comparação.

Até aqui a tabela guardava só o lado da invoice (`qtd`, `preco_invoice`) e o
VEREDITO da comparação contra o PO do Catapult (`cod_status`, `issue_codes`).
Os números do PO que produziram esse veredito — `Ordered`, `Received`,
`Invoiced Total Cost` — eram lidos na raspagem, usados em memória
(`conciliacao/reconcile_erp.py::_comparar_grupo`) e descartados. Resultado: o
painel conseguia dizer QUE uma linha divergiu em quantidade, mas não DE QUANTO
nem contra qual número — não dava para auditar uma divergência sem reabrir o
Catapult.

Colunas:
    qtd_po           Ordered do PO.
    qtd_po_recebida  Received do PO. Guardado separado porque a regra compara
                     invoice x Ordered x Received entre si (`qty_matches_po`) —
                     com só um dos dois não dá para explicar a divergência. Em
                     item de peso variável (açougue) `Received` é peso pesado na
                     doca, não caixa, e por isso fica fora da checagem.
    dif_qtd          Diferença de quantidade pela MESMA regra que decidiu o
                     veredito: caixas x Ordered no açougue, qtd x Ordered no
                     resto. Calculado no Python junto com `qty_ok` de propósito
                     — subtrair `qtd - qtd_po` no SQL daria número errado para
                     item de peso variável.
    valor_invoice    Total do GRUPO da invoice que foi comparado.
    valor_po         Invoiced Total Cost do PO.

`valor_invoice` e `valor_po` existem porque `preco_invoice` (unitário da linha)
e `preco_referencia` (total do grupo no PO) não são comparáveis entre si — quem
monta relatório lado a lado com esses dois compara preço unitário com valor de
linha. O par correto é `valor_invoice` x `valor_po`, que é o que
`compare_price` de fato confrontou para gerar `dif_unitaria`/`dif_pct`.

Quando várias linhas da invoice casam com o MESMO item do PO, a comparação é
feita pelo grupo somado: as linhas do grupo repetem `qtd_po`, `valor_po`,
`valor_invoice` e `dif_qtd`. Somar essas colunas linha a linha num painel
multiplica o valor do PO pelo tamanho do grupo — agrupe por item do PO antes.

Nada é retroativo: conciliação já gravada fica com as colunas nulas até rodar
de novo (`processo.cod_status = 56` marca nota para reconciliar).

`IF NOT EXISTS` — rodar de novo não quebra nada.

    python -m manutencao.migrar_add_colunas_po            # dry-run: só mostra o SQL
    python -m manutencao.migrar_add_colunas_po --aplicar  # roda a migração
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
ALTER TABLE {SCHEMA}.fat_conciliacao_item
    ADD COLUMN IF NOT EXISTS qtd_po          NUMERIC,
    ADD COLUMN IF NOT EXISTS qtd_po_recebida NUMERIC,
    ADD COLUMN IF NOT EXISTS dif_qtd         NUMERIC,
    ADD COLUMN IF NOT EXISTS valor_invoice   NUMERIC,
    ADD COLUMN IF NOT EXISTS valor_po        NUMERIC;
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
        print("\nColunas do lado PO prontas em fat_conciliacao_item.")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
