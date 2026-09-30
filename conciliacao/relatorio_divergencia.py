"""Relatório .docx de divergência por invoice (comparação `comparacao='erp'`).

Traduz o `resultado` de `reconcile_erp.reconcile_items_against_po` — dict com
`items`, cada um com `has_issue`/`issue_codes`/os números da comparação
(`qty_invoice`, `qty_po`, `qty_diff`, `price_invoice`, `total_invoice`,
`total_po`, `price_diff`) — para o texto que vai no documento, separado em
duas seções (quantidade x valor, os dois vereditos que `_comparar_grupo`
produz). Nota sem PO pra comparar (`PO_NAO_ENCONTRADA` no header) sai só com
o aviso no cabeçalho — sem tabela de item, ver `gerar_relatorio_divergencia_
erp`. Mesmas colunas das queries do painel Grafana de divergência ("Qtd
Invoice"/"Qtd PO"/"Diferença Qtd" e "Preço Unit Invoice"/"Valor Invoice"/
"Valor PO"/"Diferença Valor"). A escrita do arquivo em si é genérica, mora em
`commons/docx_report.py`; aqui só a regra de o que é "quantidade" e "valor"
pra esta invoice, e onde salvar.
"""

from __future__ import annotations

from decimal import Decimal
import re
from pathlib import Path

from commons.docx_report import gerar_relatorio
from commons.paths import DIVERGENCIAS_ERP_DIR
from domain.conciliacao_codes import IssueCode

_QTY = IssueCode.QTY_MISMATCH_PO
_PRECO = IssueCode.PRICE_MISMATCH_PO
_PO_NAO_ENCONTRADA = IssueCode.PO_NAO_ENCONTRADA

# id_loja -> nome, mesmo de-para do painel Grafana e de
# `domain/service/invoice_service.py::_PREFIXO_LOJA` — só essas duas lojas
# existem hoje (ver `crawler/flow/reconcile_erp_flow.py::_URL_ENV_POR_LOJA`).
NOME_LOJA = {1: "Windermere", 2: "Dr. Phillips"}


def gerar_relatorio_divergencia_erp(header: dict, resultado: dict) -> Path | None:
    """Gera o .docx desta invoice em `DIVERGENCIAS_ERP_DIR`, só quando há
    divergência (`resultado['has_issue']`). `None` quando não há — quem
    chama não precisa checar antes, só testar o retorno.

    `header`: mesmo dict de `fetch_invoice_headers_for_reconciliation`
    (`domain/service/conciliacao_service.py`) — usa `invoice_number`,
    `supplier_name`, `invoice_date`, `id_loja`.
    """
    if not resultado["has_issue"]:
        return None

    itens = resultado["items"]
    invoice_number = header.get("invoice_number") or f"id{header['id']}"
    titulo = f"Relatório de divergências — Invoice {invoice_number}"

    linhas_cabecalho = [
        f"Fornecedor: {header.get('supplier_name') or '-'}",
        f"Data: {header.get('invoice_date') or '-'}",
    ]
    nome_loja = NOME_LOJA.get(header.get("id_loja"))
    if nome_loja:
        linhas_cabecalho.append(f"Loja: {nome_loja}")
    if _PO_NAO_ENCONTRADA in resultado.get("issue_codes", []):
        linhas_cabecalho.append(
            "Aviso: nenhum PO 'Ordered' encontrado no Catapult para este fornecedor "
            "— nota inteira sem PO pra comparar.")

    secoes = [
        ("Divergência de quantidade",
         ["Descrição", "Qtd Invoice", "Qtd PO", "Diferença Qtd"],
         _linhas_quantidade(itens)),
        ("Divergência de valor",
         ["Item", "Descrição", "Preço Unit Invoice", "Valor Invoice", "Valor PO", "Diferença Valor"],
         _linhas_valor(itens)),
    ]

    caminho = DIVERGENCIAS_ERP_DIR / nome_arquivo("divergencia", header)
    return gerar_relatorio(caminho, titulo, linhas_cabecalho, secoes)


def _linhas_quantidade(itens: list[dict]) -> list[tuple[str, ...]]:
    linhas = []
    for item in itens:
        if _QTY not in (item.get("issue_codes") or ()):
            continue
        diferenca = item.get("qty_diff")
        linhas.append((
            item.get("description_invoice") or "",
            fmt(item.get("qty_invoice")),
            fmt(item.get("qty_po")),
            fmt(abs(diferenca) if diferenca is not None else None),
        ))
    return linhas


def _linhas_valor(itens: list[dict]) -> list[tuple[str, ...]]:
    linhas = []
    for item in itens:
        if _PRECO not in (item.get("issue_codes") or ()):
            continue
        linhas.append((
            str(item.get("item_order") or "-"),
            item.get("description_invoice") or "",
            fmt(item.get("price_invoice")),
            fmt(item.get("total_invoice")),
            fmt(item.get("total_po")),
            fmt(item.get("price_diff")),
        ))
    return linhas


def nome_arquivo(prefixo: str, header: dict) -> str:
    """Nome do .docx: `<prefixo>_<id da nota>_<numero>.docx`.

    O id da nota (`fat_invoice.id`) garante unicidade — o mesmo numero pode
    existir em dois fornecedores ou lojas. O numero e lido pelo Vision, entao
    so letras, digitos, `.` e `-` sobrevivem (uma `/` criaria subpasta).
    """
    numero = re.sub(r"[^A-Za-z0-9.-]+", "_", str(header.get("invoice_number") or "")).strip("_")
    return f"{prefixo}_{header['id']}_{numero or 'sem_numero'}.docx"


def fmt(valor) -> str:
    if valor is None:
        return "-"
    if isinstance(valor, Decimal):
        return f"{valor:.2f}"
    return str(valor)
