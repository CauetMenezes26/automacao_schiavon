"""Acesso a banco da conciliação — schema `dwschiavon2`.

De-para das tabelas:

    supplier_alias        -> dim_fornecedor_alias
    invoice_header        -> fat_invoice
    invoice_items         -> fat_invoice_item   (só tipo_linha = 'item')
    quotation_requests    -> dim_ciclo
    price_quote           -> fat_cotacao_preco
    reconciliation_header -> fat_conciliacao
    reconciliation_item   -> fat_conciliacao_item
    reconciliation_run    -> absorvida por `processo`

**A rodada deixou de existir.** No dwschiavon cada execução criava um
`reconciliation_run` e uma cópia dos resultados, então dez execuções do mesmo
período geravam dez versões e o BI precisava filtrar pela última. Agora a chave
é `(id_invoice, comparacao)`: reconciliar de novo atualiza a mesma linha. O
histórico de execução, que era o único ganho do run, mora em `processo`.

`issue_codes` (lista de texto) virou `cod_status` + `revisar`. A lista misturava
veredito de preço com sinalização de qualidade — `['handwritten_present',
'price_above_quote']` é uma divergência de preço numa nota que também tem
anotação à mão, e são coisas de naturezas diferentes.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import psycopg2
from psycopg2.extras import RealDictCursor

from commons.exception import DataAccessException
from domain.conciliacao_codes import MARCAS_REVISAO, IssueCode
from domain.enums import StatusConciliacaoEnum as Veredito, StatusExecEnum
from domain.service.processo_service import SCHEMA

# Marca de "reconciliar de novo" — o valor é do enum, não literal solto.
_REPROC = StatusExecEnum.REPROCESSAR_CONCILIACAO
_REPROC_SET = f"cod_status = {int(_REPROC)}, status_exec = '{_REPROC.name}'"

if TYPE_CHECKING:
    from conciliacao.sinonimos import Sinonimo


def _veredito(codes: list[str] | None) -> Veredito:
    """Traduz a lista de códigos no veredito único da linha.

    Ordem de precedência: não dá para comparar > diverge > confere.
    """
    codes = codes or []
    if "no_quote_for_item" in codes:
        return Veredito.SEM_REFERENCIA_ITEM
    if "unit_mismatch" in codes:
        return Veredito.UNIDADE_DIVERGENTE
    if "price_above_quote" in codes:
        return Veredito.PRECO_ACIMA
    if "price_below_quote" in codes:
        return Veredito.PRECO_ABAIXO
    return Veredito.CONFERIDO


def _revisar(codes: list[str] | None, needs_review: bool) -> bool:
    return bool(needs_review) or bool(MARCAS_REVISAO & set(codes or []))


# ---------------------------------------------------------------------------
# De-para de fornecedor
# ---------------------------------------------------------------------------

def fetch_supplier_aliases(conn, source: str = "invoice") -> dict[str, dict]:
    """Mapa alias_norm → {canonical_id, canonical_name, categoria}.

    A categoria vem junto porque é ela que define o escopo da conciliação. Sem
    ela o filtro era acidental: nota de papel ou bebida só ficava de fora por
    não ter alias cadastrado, e isso deixou de bastar quando o de-para passou a
    casar por aproximação.
    """
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            f"""
            SELECT a.alias_norm,
                   a.id_fornecedor AS canonical_id,
                   f.nome          AS canonical_name,
                   f.categoria
              FROM {SCHEMA}.dim_fornecedor_alias a
              JOIN {SCHEMA}.dim_fornecedor       f ON f.id = a.id_fornecedor
             WHERE a.origem = %s AND a.ativo
            """,
            (source,),
        )
        return {r["alias_norm"]: dict(r) for r in cur.fetchall()}


def save_supplier_alias(
    conn, canonical_id: int, canonical_name: str, alias: str,
    alias_norm: str, source: str,
) -> int:
    """Grava um apelido de fornecedor. Retorna o id."""
    with conn.cursor() as cur:
        cur.execute(
            f"""
            INSERT INTO {SCHEMA}.dim_fornecedor_alias
                (id_fornecedor, alias, alias_norm, origem)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (alias_norm, origem) DO UPDATE
               SET id_fornecedor = EXCLUDED.id_fornecedor,
                   alias         = EXCLUDED.alias,
                   ativo         = true
            RETURNING id
            """,
            (canonical_id, alias, alias_norm, source),
        )
        alias_id = cur.fetchone()[0]
    conn.commit()
    return alias_id


# ---------------------------------------------------------------------------
# De-para de vocabulário de item (sinônimos)
# ---------------------------------------------------------------------------

def fetch_item_sinonimos(conn) -> dict[str, str]:
    """Mapa termo_norm → canonico dos sinônimos de item ativos.

    Uma query por execução: o `matcher` consome isto em memória (via
    `match_items(..., sinonimos=...)`), nunca uma query por item.
    """
    with conn.cursor() as cur:
        cur.execute(
            f"""
            SELECT termo_norm, canonico
              FROM {SCHEMA}.dim_item_sinonimo
             WHERE ativo
            """
        )
        return {termo_norm: canonico for termo_norm, canonico in cur.fetchall()}


def upsert_item_sinonimos(conn, rows: list[Sinonimo]) -> int:
    """Grava/atualiza os sinônimos de item. Retorna quantas linhas foram enviadas.

    **Idempotente por `termo_norm`:** rodar de novo com a mesma planilha não
    duplica nem muda nada. `ON CONFLICT` cobre tanto inclusão quanto atualização
    (o time edita a planilha DE-PARA e o pipeline sincroniza a cada execução).
    """
    if not rows:
        return 0

    with conn.cursor() as cur:
        cur.executemany(
            f"""
            INSERT INTO {SCHEMA}.dim_item_sinonimo (termo, termo_norm, canonico)
            VALUES (%s, %s, %s)
            ON CONFLICT (termo_norm) DO UPDATE
               SET termo     = EXCLUDED.termo,
                   canonico  = EXCLUDED.canonico,
                   ativo     = true
            """,
            [(r.termo, r.termo_norm, r.canonico) for r in rows],
        )
    conn.commit()
    return len(rows)


# ---------------------------------------------------------------------------
# Insumos da conciliação
# ---------------------------------------------------------------------------

# Colunas que _reconcile_one_invoice espera no header. `id_fornecedor` vem junto
# para a conciliação usar o que a etapa IDENTIFICAR_FORNECEDOR já resolveu, em
# vez de re-resolver em memória. Qualificadas com `i.` porque a query da janela
# junta `processo`.
_HEADER_COLS = f"""
    i.id, i.id_processo, i.id_loja, i.id_fornecedor,
    i.numero          AS invoice_number,
    i.emissao         AS invoice_date,
    i.fornecedor_lido AS supplier_name,
    i.total           AS total_amount,
    i.confianca       AS reading_confidence
"""

# cod_status que NÃO volta para a conciliação: já finalizou (com ou sem alerta)
# ou encerrou por falta de insumo. Regra: só reconcilia quem em algum momento
# errou (faixa 50-59) ou ainda não passou pela etapa. Reprocesso explícito (56)
# vem por fetch_invoice_headers_reprocesso, fora da janela.
_NAO_RECONCILIA = (
    int(StatusExecEnum.FINALIZADO),
    int(StatusExecEnum.FINALIZADO_COM_ALERTA),
    int(StatusExecEnum.ENCERRADO_SEM_COTACAO),
    int(StatusExecEnum.ENCERRADO_SEM_ARQUIVO),
)


def fetch_invoice_headers_for_reconciliation(
    conn, date_from, date_to, supplier: str | None = None,
) -> list[dict]:
    """Notas do período que ainda precisam conciliar.

    Só entra a nota que ainda não passou pela etapa ou que em algum momento
    errou (`processo.cod_status` fora de `_NAO_RECONCILIA`). Nota já conciliada
    sem pendência não volta só por cair na janela de data.
    """
    sql = f"""
        SELECT {_HEADER_COLS}
          FROM {SCHEMA}.fat_invoice i
          JOIN {SCHEMA}.processo    p ON p.id = i.id_processo
         WHERE i.emissao BETWEEN %s AND %s
           AND p.cod_status NOT IN %s
    """
    params: list = [date_from, date_to, _NAO_RECONCILIA]
    if supplier:
        sql += " AND i.fornecedor_lido ILIKE %s"
        params.append(f"%{supplier}%")
    sql += " ORDER BY i.emissao, i.id"

    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(sql, params)
        return [dict(r) for r in cur.fetchall()]


def fetch_invoice_headers_reprocesso(conn) -> list[dict]:
    """Notas marcadas para reconciliar de novo (processo.cod_status = 56).

    Vêm fora da janela de data: um sinônimo ou alias novo pode ter destravado
    uma nota antiga. `reconcile_quote` une esta lista à da janela.
    """
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            f"""
            SELECT {_HEADER_COLS}
              FROM {SCHEMA}.fat_invoice i
             WHERE i.id_processo IN (
                       SELECT id FROM {SCHEMA}.processo
                        WHERE cod_tipo = 'invoice' AND cod_status = 56
                   )
             ORDER BY i.emissao, i.id
            """
        )
        return [dict(r) for r in cur.fetchall()]


def update_invoice_fornecedor(conn, id_invoice: int, id_fornecedor: int) -> None:
    """Grava em `fat_invoice` o fornecedor que a etapa IDENTIFICAR_FORNECEDOR
    resolveu. Fica aqui (e não em coleta_invoices) porque quem resolve é o fluxo
    da conciliação — mesma razão de as leituras de invoice para conciliar
    viverem neste módulo. O nível/score do match ficam em `fat_conciliacao`."""
    with conn.cursor() as cur:
        cur.execute(
            f"UPDATE {SCHEMA}.fat_invoice SET id_fornecedor = %s WHERE id = %s",
            (id_fornecedor, id_invoice),
        )
    conn.commit()


def fetch_invoice_items_by_headers(conn, header_ids: list[int]) -> dict[int, list[dict]]:
    """Linhas de mercadoria, agrupadas por nota.

    Filtra `tipo_linha = 'item'`: frete e subtotal não têm cotação, e comparar
    um deles produz divergência falsa.
    """
    if not header_ids:
        return {}
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            f"""
            SELECT id, id_invoice AS id_header, ordem AS item_order,
                   descricao   AS description,
                   qtd         AS quantity,
                   unidade     AS unit,
                   preco_unit  AS unit_price,
                   valor_linha AS total_price,
                   anotacao_manual AS handwritten_notes
              FROM {SCHEMA}.fat_invoice_item
             WHERE id_invoice = ANY(%s) AND tipo_linha = 'item'
             ORDER BY id_invoice, ordem NULLS LAST, id
            """,
            (header_ids,),
        )
        agrupado: dict[int, list[dict]] = {}
        for row in cur.fetchall():
            agrupado.setdefault(row["id_header"], []).append(dict(row))
    return agrupado


def fetch_request_for_date(conn, invoice_date) -> dict | None:
    """Ciclo de cotação cuja semana contém a data da nota."""
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            f"""
            SELECT id, semana_label AS week_label,
                   inicio AS week_start, fim AS week_end
              FROM {SCHEMA}.dim_ciclo
             WHERE %s BETWEEN inicio AND fim
             ORDER BY id DESC LIMIT 1
            """,
            (invoice_date,),
        )
        row = cur.fetchone()
    return dict(row) if row else None


def fetch_quote_lines(conn, id_request: int, id_supplier: int) -> list[dict]:
    """Preços cotados por um fornecedor num ciclo. Zero significa 'não cotado'."""
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            f"""
            SELECT id,
                   item_codigo AS item_code,
                   item_nome   AS item_name,
                   preco       AS price,
                   preco_raw   AS price_raw,
                   NULL::date  AS quote_date
              FROM {SCHEMA}.fat_cotacao_preco
             WHERE id_ciclo = %s AND id_fornecedor = %s AND preco > 0
             ORDER BY item_nome
            """,
            (id_request, id_supplier),
        )
        return [dict(r) for r in cur.fetchall()]


# ---------------------------------------------------------------------------
# Resultado
# ---------------------------------------------------------------------------

def save_reconciliation_header(conn, data: dict) -> int:
    """Grava a nota conciliada. Retorna o id.

    Sem `id_run`: a chave é (id_invoice, comparacao), então reconciliar de novo
    atualiza em vez de acumular versões.
    """
    veredito = _veredito(data.get("issue_codes"))
    with conn.cursor() as cur:
        cur.execute(
            f"""
            INSERT INTO {SCHEMA}.fat_conciliacao (
                id_invoice, id_ciclo, id_loja, id_fornecedor, id_processo,
                comparacao, cod_status, status_conc, revisar,
                issue_codes, match_nivel, match_score
            ) VALUES (%s, %s, %s, %s, %s, 'cotacao', %s, %s, %s, %s, %s, %s)
            ON CONFLICT (id_invoice, comparacao) DO UPDATE SET
                id_ciclo      = EXCLUDED.id_ciclo,
                id_fornecedor = EXCLUDED.id_fornecedor,
                id_processo   = EXCLUDED.id_processo,
                cod_status    = EXCLUDED.cod_status,
                status_conc   = EXCLUDED.status_conc,
                revisar       = EXCLUDED.revisar,
                issue_codes   = EXCLUDED.issue_codes,
                match_nivel   = EXCLUDED.match_nivel,
                match_score   = EXCLUDED.match_score,
                criado_em     = now() AT TIME ZONE 'America/Sao_Paulo'
            RETURNING id
            """,
            (
                data["id_invoice_header"], data.get("id_request"),
                data["id_loja"], data.get("id_supplier"), data.get("id_processo"),
                int(veredito), veredito.name,
                _revisar(data.get("issue_codes"), data.get("needs_review", False)),
                data.get("issue_codes") or None,
                data.get("match_level"),
                data.get("supplier_match_score"),
            ),
        )
        id_conc = cur.fetchone()[0]
    conn.commit()
    return id_conc


def save_reconciliation_items(conn, id_recon_header: int, rows: list[dict]) -> int:
    """Grava as linhas comparadas. Calcula `dif_valor`, que é o que o BI soma."""
    if not rows:
        return 0

    dados = []
    for r in rows:
        veredito = _veredito(r.get("issue_codes"))
        dif, qtd = r.get("price_diff"), r.get("qty_invoice")
        dif_valor = (dif * qtd) if (dif is not None and qtd is not None) else None
        dados.append((
            id_recon_header, r["id_invoice_item"], r.get("id_price_quote"),
            r.get("description_invoice"), r.get("item_name_quote"),
            r.get("match_level"), r.get("match_score"), qtd,
            r.get("price_invoice"), r.get("price_other"), r.get("price_other_raw"),
            dif, r.get("price_diff_pct"), dif_valor,
            int(veredito), veredito.name,
            _revisar(r.get("issue_codes"), r.get("needs_review", False)),
            r.get("issue_codes") or None,
        ))

    with conn.cursor() as cur:
        cur.executemany(
            f"""
            INSERT INTO {SCHEMA}.fat_conciliacao_item (
                id_conciliacao, id_invoice_item, id_cotacao_preco,
                descricao_invoice, item_referencia, match_nivel, match_score,
                qtd, preco_invoice, preco_referencia, preco_ref_raw,
                dif_unitaria, dif_pct, dif_valor, cod_status, status_conc, revisar,
                issue_codes
            ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT (id_conciliacao, id_invoice_item) DO UPDATE SET
                id_cotacao_preco = EXCLUDED.id_cotacao_preco,
                item_referencia  = EXCLUDED.item_referencia,
                match_nivel      = EXCLUDED.match_nivel,
                match_score      = EXCLUDED.match_score,
                preco_referencia = EXCLUDED.preco_referencia,
                dif_unitaria     = EXCLUDED.dif_unitaria,
                dif_pct          = EXCLUDED.dif_pct,
                dif_valor        = EXCLUDED.dif_valor,
                cod_status       = EXCLUDED.cod_status,
                status_conc      = EXCLUDED.status_conc,
                revisar          = EXCLUDED.revisar,
                issue_codes      = EXCLUDED.issue_codes
            """,
            dados,
        )
    conn.commit()
    return len(dados)


# ---------------------------------------------------------------------------
# Reprocesso automático (G10) — limitado ao que a correção pode destravar
# ---------------------------------------------------------------------------

# Teto de segurança: uma edição grande da planilha DE-PARA não pode reenfileirar
# a base inteira. Acima disto, o pipeline avisa e NÃO remarca — pede rodada manual.
LIMITE_REPROCESSO = 200


def _contar(conn, sql: str) -> int:
    with conn.cursor() as cur:
        cur.execute(sql)
        return cur.fetchone()[0]


def marcar_reprocesso_por_vocabulario(conn) -> int:
    """Remarca para reconciliar as notas que um sinônimo novo pode destravar.

    Escopo: notas já finalizadas (`cod_status` 0 ou 1) cuja conciliação tem ao
    menos uma linha `unmatched` ou `SEM_REFERENCIA_ITEM` (20). São as únicas em
    que traduzir um termo pode mudar o resultado. Retorna quantas foram
    remarcadas; -1 quando o total passa de LIMITE_REPROCESSO (nada é feito).
    """
    alvo = f"""
        SELECT p.id
          FROM {SCHEMA}.processo p
         WHERE p.cod_tipo = 'invoice' AND p.cod_status IN (0, 1)
           AND EXISTS (
               SELECT 1
                 FROM {SCHEMA}.fat_invoice fi
                 JOIN {SCHEMA}.fat_conciliacao      fc  ON fc.id_invoice = fi.id
                 JOIN {SCHEMA}.fat_conciliacao_item fci ON fci.id_conciliacao = fc.id
                WHERE fi.id_processo = p.id
                  AND (fci.match_nivel = 'unmatched' OR fci.cod_status = 20)
           )
    """
    total = _contar(conn, f"SELECT count(*) FROM ({alvo}) t")
    if total == 0:
        return 0
    if total > LIMITE_REPROCESSO:
        return -1
    with conn.cursor() as cur:
        cur.execute(
            f"""
            UPDATE {SCHEMA}.processo
               SET {_REPROC_SET},
                   atualizado_em = now() AT TIME ZONE 'America/Sao_Paulo'
             WHERE id IN ({alvo})
            """
        )
        n = cur.rowcount
    conn.commit()
    return n


def marcar_reprocesso_por_fornecedor(conn) -> int:
    """Remarca as notas que ficaram sem fornecedor (etapa 13 falhou,
    `cod_status` 55) para reconciliar de novo — chamado após aprovar aliases
    novos. Bounded por natureza: só as que já estavam nesse estado."""
    with conn.cursor() as cur:
        cur.execute(
            f"""
            UPDATE {SCHEMA}.processo
               SET {_REPROC_SET},
                   atualizado_em = now() AT TIME ZONE 'America/Sao_Paulo'
             WHERE cod_tipo = 'invoice' AND cod_status = {int(StatusExecEnum.ERRO_SEM_FORNECEDOR)}
            """
        )
        n = cur.rowcount
    conn.commit()
    return n


def marcar_reprocesso_por_cotacao(conn) -> int:
    """Remarca as notas que fecharam com `no_quote_for_supplier` para
    reconciliar de novo — chamado depois que `check_responses` importa preço
    novo. `ENCERRADO_SEM_COTACAO` nunca é o status usado aqui de verdade: sem
    cotação sempre liga `needs_review`, então a nota fecha como
    `FINALIZADO_COM_ALERTA` (o mesmo status de qualquer outro alerta) — o
    jeito de achar só as que fecharam por falta de cotação é olhar
    `fat_conciliacao.issue_codes`, não `processo.cod_status`. Sem isto a nota
    fica presa: `FINALIZADO_COM_ALERTA` está em `_NAO_RECONCILIA`, e nada
    além disto a devolve pra janela de data."""
    with conn.cursor() as cur:
        cur.execute(
            f"""
            UPDATE {SCHEMA}.processo p
               SET {_REPROC_SET},
                   atualizado_em = now() AT TIME ZONE 'America/Sao_Paulo'
              FROM {SCHEMA}.fat_invoice i
              JOIN {SCHEMA}.fat_conciliacao fc ON fc.id_invoice = i.id
             WHERE p.id = i.id_processo
               AND fc.issue_codes @> ARRAY['{IssueCode.NO_QUOTE_FOR_SUPPLIER}']::text[]
            """
        )
        n = cur.rowcount
    conn.commit()
    return n


# ---------------------------------------------------------------------------
# Relatorio Cotacao x Invoice — leitura agregada, sem tocar na escrita acima
#
# Duas abas, duas consultas, sem interseccao (StatusConciliacaoEnum em
# domain/enums.py):
#   `fetch_comparacao_precos`  aba 1 — so o que conciliou: CONFERIDO (0)
#   `fetch_divergencias`       aba 2 — so o que precisa de acao: preco fora da
#                              tolerancia (10-19) e item sem comparacao (20-29)
# ---------------------------------------------------------------------------

_DIVERGENTE = (int(Veredito.PRECO_ACIMA), int(Veredito.PRECO_ABAIXO))
_SEM_COMPARACAO = (int(Veredito.SEM_REFERENCIA_ITEM), int(Veredito.UNIDADE_DIVERGENTE))


_CONCILIADO = (int(Veredito.CONFERIDO),)
_DIVERGENCIA = _DIVERGENTE + _SEM_COMPARACAO

# Colunas comuns às duas abas — o relatório é o mesmo item visto de dois
# ângulos, então nome de coluna diferente entre as abas só confundiria.
_COLS_RELATORIO = """
                       fi.arquivo,
                       COALESCE(df.nome, fi.fornecedor_lido) AS fornecedor,
                       ci.descricao_invoice                  AS item,
                       ci.item_referencia                    AS item_cotado,
                       ci.qtd,
                       ci.preco_invoice, ci.preco_referencia,
                       ci.dif_unitaria, ci.dif_pct, ci.dif_valor,
                       ci.cod_status
"""

_FROM_RELATORIO = """
                  FROM {schema}.fat_conciliacao_item ci
                  JOIN {schema}.fat_conciliacao      fc ON fc.id = ci.id_conciliacao
                  JOIN {schema}.fat_invoice          fi ON fi.id = fc.id_invoice
                  LEFT JOIN {schema}.dim_fornecedor  df ON df.id = fc.id_fornecedor
"""


def fetch_comparacao_precos(conn, inicio, fim) -> list[dict]:
    """Aba "Cotacao x Invoice": uma linha por item que **conciliou** (preço
    cotado x preço da invoice), com o arquivo de origem.

    Só `CONFERIDO` — preço dentro da tolerância. Tudo o que não fechou (preço
    fora da tolerância, item sem par, unidade divergente) vive em
    `fetch_divergencias`, e as duas abas não se sobrepõem: quem abre a primeira
    está vendo o que deu certo, não a base inteira.

    **Preço unitário** (`preco_invoice`/`preco_referencia` de
    `fat_conciliacao_item`) — não o valor total da linha, que mistura
    quantidade com preço.
    """
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                f"""
                SELECT {_COLS_RELATORIO}
                {_FROM_RELATORIO.format(schema=SCHEMA)}
                 WHERE ci.cod_status IN %s
                   AND ci.criado_em::date BETWEEN %s AND %s
                 ORDER BY fi.arquivo, ci.descricao_invoice
                """,
                (_CONCILIADO, inicio, fim),
            )
            return [dict(r) for r in cur.fetchall()]
    except psycopg2.Error as exc:
        raise DataAccessException(
            "painel - falha ao consultar comparacao de precos"
        ) from exc


def fetch_divergencias(conn, inicio, fim) -> list[dict]:
    """Aba "Divergencias Cotacao x Invoice": só o item que não fechou.

    Junta as duas naturezas de problema numa lista de trabalho única, porque
    para quem confere a nota as duas terminam no mesmo lugar — ligar para o
    fornecedor ou corrigir o de-para:

        10-19  preço fora da tolerância  -> tem os dois preços e a diferença
        20-29  sem comparação            -> `preco_referencia` vem nulo

    Ordena pelo maior impacto em valor (`dif_valor`), com os sem comparação no
    fim: quem abre a aba vê primeiro o que custa dinheiro.
    """
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                f"""
                SELECT {_COLS_RELATORIO}
                {_FROM_RELATORIO.format(schema=SCHEMA)}
                 WHERE ci.cod_status IN %s
                   AND ci.criado_em::date BETWEEN %s AND %s
                 ORDER BY ABS(COALESCE(ci.dif_valor, 0)) DESC,
                          fi.arquivo, ci.descricao_invoice
                """,
                (_DIVERGENCIA, inicio, fim),
            )
            return [dict(r) for r in cur.fetchall()]
    except psycopg2.Error as exc:
        raise DataAccessException(
            "painel - falha ao consultar divergencias cotacao x invoice"
        ) from exc
