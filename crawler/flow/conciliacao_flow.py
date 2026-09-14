"""FLUXO 4 — Conciliação Cotação × Invoice.

Compara o preço que o fornecedor cobrou na invoice com o preço que ele cotou na
semana correspondente, item a item, e grava em `fat_conciliacao` / `_item`.

Lê do banco, então roda mesmo que a coleta (FLUXO 2) tenha falhado — só com
dados um pouco mais velhos. Não depende do ERP.

O motor de match e a conciliação de uma nota moram em `conciliacao/` (motor puro
em `commons/matcher.py`, regras da nota em `conciliacao/reconcile_quote.py`).
Aqui a orquestração do período + o contrato de fluxo.
"""

from __future__ import annotations

from datetime import date

from commons.db import connect_db
from commons.exception import BusinessException
from commons.logging_config import get_logger
from conciliacao.reconcile_quote import (
    NO_QUOTE_FOR_SUPPLIER,
    SUPPLIER_ERP_ONLY,
    SUPPLIER_FUZZY_MATCH,
    SUPPLIER_UNMAPPED,
    _reconcile_one_invoice,
    _week_bounds,
)
from domain.enums import EtapaEnum as Etapa, StatusExecEnum as Status
from domain.service import processo_service as proc
from domain.service.conciliacao_service import (
    fetch_invoice_headers_for_reconciliation,
    fetch_invoice_headers_reprocesso,
    fetch_invoice_items_by_headers,
    fetch_item_sinonimos,
    fetch_supplier_aliases,
    save_reconciliation_header,
    save_reconciliation_items,
    update_invoice_fornecedor,
)

log = get_logger(__name__)


def conciliacao_flow(
    env: dict[str, str],
    date_from: date | None = None,
    date_to: date | None = None,
    supplier: str | None = None,
) -> dict:
    """Roda a conciliação do período e grava o resultado. Retorna os totais."""
    if date_from is None or date_to is None:
        # inicio, fim = _week_bounds(date.today())  # MOCK TEMPORÁRIO: voltar esta linha depois do teste
        # MOCK: cobre as duas semanas de teste (notas de 19/06 e de 25-26/06) —
        # voltar para a linha acima (date.today()) depois de aprovar o teste.
        inicio, fim = date(2026, 6, 25), date(2026, 6, 28)
        date_from = date_from or inicio
        date_to = date_to or fim

    conn = connect_db(env)
    try:

        print(f"\n{'='*60}")
        print(f"Conciliação Cotação x Invoice — {date_from} a {date_to}"
              + (f"  [fornecedor: {supplier}]" if supplier else ""))
        print(f"{'='*60}")

        aliases = fetch_supplier_aliases(conn, source="invoice")
        if not aliases:
            print("  ⚠ supplier_alias vazia — rode "
                  "'python -m cotacao.seed_supplier_alias' e aprove o CSV.")

        sinonimos = fetch_item_sinonimos(conn)
        if not sinonimos:
            print("  ⚠ dim_item_sinonimo vazia — o match de item vai sobre texto "
                  "cru. Rode 'python -m manutencao.seed_item_sinonimos --aplicar' "
                  "ou deixe files/sinonimos/DE_PARA_ITENS.xlsx no lugar.")

        headers = fetch_invoice_headers_for_reconciliation(
            conn, date_from, date_to, supplier,
        )
        # Notas remarcadas por correção de vocabulário/de-para entram fora da
        # janela — um sinônimo novo pode ter destravado uma nota antiga (G10).
        if supplier is None:
            vistos = {h["id"] for h in headers}
            remarcadas = [h for h in fetch_invoice_headers_reprocesso(conn)
                          if h["id"] not in vistos]
            if remarcadas:
                headers += remarcadas
                print(f"  + {len(remarcadas)} nota(s) remarcada(s) para reprocesso")

        if not headers:
            print("  Nenhuma invoice no período.")
            return {"headers_total": 0}

        items_by_header = fetch_invoice_items_by_headers(conn, [h["id"] for h in headers])
        print(f"  {len(headers)} invoice(s) a conciliar")


        totais = {
            "headers_total": 0, "headers_issue": 0,
            "items_total": 0, "items_issue": 0, "quote_items_unused": 0,
            "sem_fornecedor": 0, "sem_cotacao": 0, "por_aproximacao": 0,
            "para_o_erp": 0,
        }
        usados: set[int] = set()
        cotados: set[int] = set()
        maiores: list[tuple] = []

        for header in headers:
            resultado = _reconcile_one_invoice(
                conn, header, items_by_header.get(header["id"], []), aliases, sinonimos,
            )
            codigos = resultado["header"]["issue_codes"]

            id_forn = resultado["header"].get("id_supplier")

            if SUPPLIER_UNMAPPED in codigos:
                totais["sem_fornecedor"] += 1
                # Deixa de sumir do relatório: a etapa 13 falha, a nota fica com
                # cod_status ERRO_SEM_FORNECEDOR (55) — faixa 50-59, reprocessável
                # — e alimenta o gatilho de reprocesso por alias (marcar_reprocesso
                # _por_fornecedor).
                proc.falhar_etapa(
                    conn, header["id_processo"], Etapa.IDENTIFICAR_FORNECEDOR,
                    Status.ERRO_SEM_FORNECEDOR,
                    f"nome '{header.get('supplier_name')}' não casou com nenhum alias")
                continue

            # Fornecedor resolvido: grava em fat_invoice e conclui a etapa 13,
            # antes de qualquer desfecho (carne ou ERP-only).
            if id_forn:
                update_invoice_fornecedor(conn, header["id"], id_forn)
                proc.concluir_etapa(
                    conn, header["id_processo"], Etapa.IDENTIFICAR_FORNECEDOR)

            # Fornecedor conhecido, mas não-carne: a comparação dele é contra o
            # ERP, não contra cotação. Não grava linha aqui — quem grava é o
            # fluxo de ERP, com `comparacao = 'erp'`. Não é pendência. Fica em
            # etapa 13 (identificado), à espera do fluxo de ERP.
            if SUPPLIER_ERP_ONLY in codigos:
                totais["para_o_erp"] += 1
                continue

            if SUPPLIER_FUZZY_MATCH in codigos:
                totais["por_aproximacao"] += 1

            recon_header_id = save_reconciliation_header(conn, resultado["header"])
            # a nota fecha o ciclo dela aqui: conciliar e a ultima etapa
            proc.concluir_etapa(
                conn, header["id_processo"], Etapa.CONCILIAR_COTACAO,
                com_alerta=resultado["header"].get("needs_review", False))
            totais["headers_total"] += 1
            if resultado["header"]["has_issue"]:
                totais["headers_issue"] += 1

            if NO_QUOTE_FOR_SUPPLIER in codigos:
                totais["sem_cotacao"] += 1
                print(f"  ⚠ {header['supplier_name'][:38]:<40} "
                      f"{header['invoice_date']}  sem cotação no ciclo")
                continue

            save_reconciliation_items(conn, recon_header_id, resultado["items"])
            cotados.update(resultado["quote_lines"])
            usados.update(r["id_price_quote"] for r in resultado["items"]
                          if r.get("id_price_quote"))

            com_issue = sum(1 for r in resultado["items"] if r["has_issue"])
            totais["items_total"] += len(resultado["items"])
            totais["items_issue"] += com_issue

            for r in resultado["items"]:
                if r.get("price_diff_pct") is not None and r["has_issue"]:
                    maiores.append((abs(float(r["price_diff_pct"])),
                                    resultado["header"]["supplier_canonical"],
                                    r["description_invoice"], r["item_name_quote"],
                                    r["price_invoice"], r["price_other"],
                                    float(r["price_diff_pct"])))

            status = "✗" if com_issue else "✓"
            print(f"  {status} {resultado['header']['supplier_canonical'][:22]:<24} "
                  f"{header['invoice_date']}  nº {str(header['invoice_number'])[:12]:<14} "
                  f"{len(resultado['items']):>3} item(ns), {com_issue} com diferença")

        totais["quote_items_unused"] = len(cotados - usados)
        # sem rodada para fechar: o historico de execucao mora em `processo`

        print(f"\n{'-'*60}")
        print(f"  Invoices conciliadas   : {totais['headers_total']}")
        print(f"    com alguma pendência : {totais['headers_issue']}")
        print(f"    fornecedor por aproximação (conferir): {totais['por_aproximacao']}")
        print(f"  Itens comparados       : {totais['items_total']}")
        print(f"    com pendência        : {totais['items_issue']}")
        print(f"  Cotados e não faturados: {totais['quote_items_unused']}")
        print(f"  Sem cotação no ciclo   : {totais['sem_cotacao']}")
        # Estas duas não são perda: nota não-carne se concilia contra o ERP, e
        # é lá que ela vai ser comparada. Só não é AQUI.
        print(f"\n  Não entram nesta comparação (seguem para o ERP):")
        print(f"    fornecedor não-carne : {totais['para_o_erp']}")
        print(f"    fornecedor sem cadastro: {totais['sem_fornecedor']}")

        if maiores:
            maiores.sort(reverse=True)
            print(f"\n  --- 10 maiores diferenças ---")
            print(f"  {'FORNECEDOR':<20}{'INV':>9}{'COTACAO':>9}{'DIF%':>8}  ITEM")
            for _, forn, desc, cot, p_inv, p_cot, pct in maiores[:10]:
                print(f"  {str(forn)[:18]:<20}{float(p_inv):>9.2f}{float(p_cot):>9.2f}"
                      f"{pct:>7.1f}%  {str(desc)[:40]}")
                print(f"  {'':<20}{'':>9}{'':>9}{'':>8}  -> {str(cot)[:40]}")

        print(f"{'='*60}\n")
        return totais

    except BusinessException as exc:
        log.warning("conciliacao: caso de negocio - %s", exc)
        return {"headers_total": 0, "erro_negocio": str(exc)}
    finally:
        _fechar(conn)


def _fechar(conn) -> None:
    try:
        conn.close()
    except Exception:  # noqa: BLE001
        log.warning("conciliacao: falha ao fechar conexao", exc_info=True)


# Compat: nome antigo da fachada.
reconcile_quote = conciliacao_flow
