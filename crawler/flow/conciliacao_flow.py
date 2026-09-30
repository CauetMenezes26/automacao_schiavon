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

from commons.datas import week_bounds
from commons.db import connect_db, fechar
from commons.exception import BusinessException
from commons.logging_config import get_logger
from commons.sharepoint import last_week_reference
from commons.sheets import SheetsError
from conciliacao.pendentes_sinonimo import extrair_pendentes, sincronizar_pendentes
from conciliacao.reconcile_quote import (
    NO_QUOTE_FOR_SUPPLIER,
    SUPPLIER_ERP_ONLY,
    SUPPLIER_FUZZY_MATCH,
    SUPPLIER_UNMAPPED,
    reconcile_one_invoice,
)
from domain.config import Config
from domain.enums import Etapa, StatusExecucao as Status
from domain.service import processo_service as proc
from domain.service import sistema_service
from domain.service.conciliacao_service import (
    fetch_invoice_headers_for_reconciliation,
    fetch_invoice_headers_reprocesso,
    fetch_invoice_items_by_headers,
    fetch_item_sinonimos,
    fetch_supplier_aliases,
    save_reconciliation_header,
    save_reconciliation_items,
)
from domain.sistemas import Sistema

log = get_logger(__name__)


def conciliacao_flow(
    config: Config,
    date_from: date | None = None,
    date_to: date | None = None,
    supplier: str | None = None,
) -> dict:
    """Roda a conciliação do período e grava o resultado. Retorna os totais."""
    if date_from is None or date_to is None:
        # TESTE: semana passada (dia 14) — mesmo período do FLUXO 2
        # (crawler/flow/invoices_flow.py). Voltar para `week_bounds(date.today())`
        # quando sair de teste.
        inicio, fim = week_bounds(last_week_reference(date.today()))
        date_from = date_from or inicio
        date_to = date_to or fim

    conn = connect_db(config.banco)
    try:

        log.info("conciliacao cotacao x invoice - %s a %s%s", date_from, date_to,
                 f" [fornecedor: {supplier}]" if supplier else "")

        aliases = fetch_supplier_aliases(conn, source="invoice")
        if not aliases:
            log.warning("supplier_alias vazia - rode 'python -m cotacao.seed_supplier_alias' e aprove o CSV.")

        sinonimos = fetch_item_sinonimos(conn)
        if not sinonimos:
            log.warning("dim_item_sinonimo vazia - o match de item vai sobre texto cru. Confira se SINONIMOS_SHEET_ID esta no .config e se a aba 'De-Para' da planilha tem conteudo.")

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
                log.info("+ %s nota(s) remarcada(s) para reprocesso", len(remarcadas))

        if not headers:
            log.info("Nenhuma invoice no periodo.")
            return {"headers_total": 0}

        items_by_header = fetch_invoice_items_by_headers(conn, [h["id"] for h in headers])
        log.info("%s invoice(s) a conciliar", len(headers))

        sheet_id = config.sinonimos_sheet_id or None
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
            resultado = reconcile_one_invoice(
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

            # Fornecedor resolvido: conclui a etapa 13, antes de qualquer
            # desfecho (carne ou ERP-only). `fat_invoice.id_fornecedor` nao existe
            # mais, entao nada e gravado na invoice.
            if id_forn:
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
                log.warning(
                    "%-40s %s sem cotacao no ciclo",
                    header['supplier_name'][:38], header['invoice_date'],
                )
                continue

            save_reconciliation_items(conn, recon_header_id, resultado["items"])
            _sincronizar_pendentes(resultado["header"], resultado["items"], sheet_id, totais)
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
            log.info(
                "%s %-24s %s no %-14s %3s item(ns), %s com diferenca",
                status, resultado['header']['supplier_canonical'][:22], header['invoice_date'], str(header['invoice_number'])[:12], len(resultado['items']), com_issue,
            )

        totais["quote_items_unused"] = len(cotados - usados)
        # sem rodada para fechar: o historico de execucao mora em `processo`

        if totais.get("sheets_tentativas"):
            sem_erro = not totais.get("sheets_erro")
            sistema_service.registrar_acesso(
                conn, Sistema.GOOGLE_SHEETS, ok=sem_erro,
                mensagem=None if sem_erro else
                f"{totais['sheets_erro']} falha(s) sincronizando pendentes",
            )

        log.info("Invoices conciliadas : %s", totais['headers_total'])
        log.info("com alguma pendencia : %s", totais['headers_issue'])
        log.info("fornecedor por aproximacao (conferir): %s", totais['por_aproximacao'])
        log.info("Itens comparados : %s", totais['items_total'])
        log.info("com pendencia : %s", totais['items_issue'])
        log.info("Cotados e nao faturados: %s", totais['quote_items_unused'])
        log.info("Sem cotacao no ciclo : %s", totais['sem_cotacao'])
        # Estas duas não são perda: nota não-carne se concilia contra o ERP, e
        # é lá que ela vai ser comparada. Só não é AQUI.
        log.info("Nao entram nesta comparacao (seguem para o ERP):")
        log.info("fornecedor nao-carne : %s", totais['para_o_erp'])
        log.info("fornecedor sem cadastro: %s", totais['sem_fornecedor'])

        if maiores:
            maiores.sort(reverse=True)
            log.info("--- 10 maiores diferencas ---")
            log.info("%-20s%9s%9s%8s ITEM", 'FORNECEDOR', 'INV', 'COTACAO', 'DIF%')
            for _, forn, desc, cot, p_inv, p_cot, pct in maiores[:10]:
                log.info(
                    "%-20s%9.2f%9.2f%7.1f%% %s",
                    str(forn)[:18], float(p_inv), float(p_cot), pct, str(desc)[:40],
                )
                log.info("%-20s%9s%9s%8s -> %s", '', '', '', '', str(cot)[:40])

        return totais

    except BusinessException as exc:
        log.warning("conciliacao: caso de negocio - %s", exc)
        return {"headers_total": 0, "erro_negocio": str(exc)}
    finally:
        fechar(conn)


def _sincronizar_pendentes(
    header: dict, items: list[dict], sheet_id: str | None, totais: dict,
) -> None:
    """Registra na aba `Pendentes` os itens desta nota sem par na cotação.

    Secundário ao resultado principal: nunca propaga. Mesmo padrão de
    `crawler/flow/reconcile_erp_flow.py::_sincronizar_pendentes` — o acesso
    agregado ao Sheets é registrado uma vez só, no fim do fluxo.
    """
    if not sheet_id:
        return
    try:
        pendentes = extrair_pendentes(
            items, header["supplier_canonical"], "cotacao",
            header.get("invoice_date") or date.today(),
        )
        n = sincronizar_pendentes(sheet_id, pendentes)
        totais["sheets_tentativas"] = totais.get("sheets_tentativas", 0) + 1
        if n:
            log.info("(%s pendencia(s) nova(s) na aba Pendentes)", n)
    except SheetsError as exc:
        totais["sheets_tentativas"] = totais.get("sheets_tentativas", 0) + 1
        totais["sheets_erro"] = totais.get("sheets_erro", 0) + 1
        log.warning("falha sincronizando pendentes no Sheets: %s", exc)



# Compat: nome antigo da fachada.
reconcile_quote = conciliacao_flow
