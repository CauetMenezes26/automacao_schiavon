"""Catálogo de itens do Catapult (`dim_item_catapult`).

Populado por `manutencao/catapult_inventory_scrape.py` (raspagem da tela
Inventory). Mesmo padrão de `conciliacao_service.fetch_item_sinonimos` /
`upsert_item_sinonimos`: upsert idempotente por chave natural, `ativo` em vez
de delete.
"""

from __future__ import annotations

from typing import NamedTuple

from psycopg2.extras import execute_values

from domain.service.processo_service import SCHEMA


class CatapultItem(NamedTuple):
    """Uma linha da grade Inventory, como raspada."""

    catapult_item_id: str
    receipt_alias: str | None
    item_name: str
    size: str | None
    department: str | None
    brand: str | None


def fetch_catapult_items(conn, id_loja: int) -> list[dict]:
    """Itens ativos do catálogo de uma loja."""
    with conn.cursor() as cur:
        cur.execute(
            f"""
            SELECT catapult_item_id, receipt_alias, supplier_unit_id, item_name,
                   size, department, brand, base_price
              FROM {SCHEMA}.dim_item_catapult
             WHERE id_loja = %s AND ativo
            """,
            (id_loja,),
        )
        cols = [c.name for c in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]


def upsert_catapult_items(conn, id_loja: int, rows: list[CatapultItem]) -> int:
    """Grava/atualiza o catálogo raspado. Retorna quantas linhas foram enviadas.

    Idempotente por `(id_loja, catapult_item_id)`. Só escreve nas colunas que a
    raspagem realmente preenche (`receipt_alias`, `item_name`, `size`,
    `department`, `brand`) — nunca toca `supplier_unit_id`/`base_price`, que
    vêm de outra fonte ainda não implementada.

    Grava em lote com `execute_values` (uma unica instrucao VALUES por lote,
    em vez de uma round-trip de rede por linha) — com ~5000 itens o
    `executemany` puro do psycopg2 (uma INSERT por linha) levava minutos.
    """
    if not rows:
        return 0

    # dedupe por chave natural, mantendo a ultima ocorrencia — um mesmo
    # catapult_item_id duas vezes no mesmo lote de VALUES faz o Postgres
    # rejeitar com "ON CONFLICT DO UPDATE command cannot affect row a second
    # time".
    por_chave = {r.catapult_item_id: r for r in rows}
    unicas = list(por_chave.values())

    with conn.cursor() as cur:
        execute_values(
            cur,
            f"""
            INSERT INTO {SCHEMA}.dim_item_catapult
                (id_loja, catapult_item_id, receipt_alias, item_name, size,
                 department, brand)
            VALUES %s
            ON CONFLICT (id_loja, catapult_item_id) DO UPDATE
               SET receipt_alias = EXCLUDED.receipt_alias,
                   item_name     = EXCLUDED.item_name,
                   size          = EXCLUDED.size,
                   department    = EXCLUDED.department,
                   brand         = EXCLUDED.brand,
                   ativo         = true,
                   atualizado_em = (now() AT TIME ZONE 'America/Sao_Paulo')
            """,
            [
                (id_loja, r.catapult_item_id, r.receipt_alias, r.item_name,
                 r.size, r.department, r.brand)
                for r in unicas
            ],
            page_size=500,
        )
    conn.commit()
    return len(unicas)


def contar_catapult_items(conn, id_loja: int) -> int:
    """Total de linhas ativas em `dim_item_catapult` pra uma loja — leitura
    direta do banco, pra confirmar o que realmente foi persistido (nao só o
    que o upsert enviou)."""
    with conn.cursor() as cur:
        cur.execute(
            f"SELECT count(*) FROM {SCHEMA}.dim_item_catapult WHERE id_loja = %s AND ativo",
            (id_loja,),
        )
        return cur.fetchone()[0]


def desativar_catapult_items_sumidos(conn, id_loja: int, catapult_item_ids: list[str]) -> int:
    """Marca `ativo=false` para itens da loja que NAO vieram na última
    raspagem. Só chamar quando `catapult_item_ids` representa o catálogo
    INTEIRO daquela loja (raspagem parcial marcaria o resto como sumido por
    engano)."""
    with conn.cursor() as cur:
        cur.execute(
            f"""
            UPDATE {SCHEMA}.dim_item_catapult
               SET ativo = false,
                   atualizado_em = (now() AT TIME ZONE 'America/Sao_Paulo')
             WHERE id_loja = %s AND ativo AND NOT (catapult_item_id = ANY(%s))
            """,
            (id_loja, catapult_item_ids),
        )
        n = cur.rowcount
    conn.commit()
    return n
