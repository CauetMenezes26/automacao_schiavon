"""Relatório .docx de invoice 100% conciliada (comparação `comparacao='erp'`).

Par de `relatorio_divergencia.py`, mutuamente exclusivo com ele: o veredito da
nota (`resultado['has_issue']` de `reconcile_erp.reconcile_items_against_po`)
decide qual dos dois sai. Nota sem nenhum item divergente — e com pelo menos um
item comparado — gera este; qualquer divergência (inclusive nota sem PO) gera o
outro. Mesmas colunas do relatório de divergência de valor/quantidade, lado a
lado numa tabela só. A escrita do arquivo é genérica, mora em
`commons/docx_report.py`.
"""

from __future__ import annotations

from pathlib import Path

from commons.docx_report import gerar_relatorio
from commons.paths import SUCESSOS_ERP_DIR
from conciliacao.relatorio_divergencia import NOME_LOJA, fmt, nome_arquivo

_COLUNAS = [
    "Item", "Descrição Invoice", "Descrição PO", "Qtd Invoice", "Qtd PO",
    "Preço Unit Invoice", "Valor Invoice", "Valor PO",
]


def gerar_relatorio_sucesso_erp(header: dict, resultado: dict) -> Path | None:
    """Gera o .docx desta invoice em `SUCESSOS_ERP_DIR`, só quando a nota
    inteira conciliou sem divergência. `None` quando há divergência ou quando
    não há item algum a listar — quem chama não precisa checar antes.

    `header`: mesmo dict de `fetch_invoice_headers_for_reconciliation` —
    usa `id`, `invoice_number`, `supplier_name`, `invoice_date`, `id_loja`.
    """
    itens = resultado["items"]
    if resultado["has_issue"] or not itens:
        return None

    invoice_number = header.get("invoice_number") or f"id{header['id']}"
    titulo = f"Relatório de conciliação — Invoice {invoice_number}"

    linhas_cabecalho = [
        f"Fornecedor: {header.get('supplier_name') or '-'}",
        f"Data: {header.get('invoice_date') or '-'}",
    ]
    nome_loja = NOME_LOJA.get(header.get("id_loja"))
    if nome_loja:
        linhas_cabecalho.append(f"Loja: {nome_loja}")

    secoes = [("Itens conciliados", _COLUNAS, _linhas(itens))]
    caminho = SUCESSOS_ERP_DIR / nome_arquivo("sucesso", header)
    return gerar_relatorio(caminho, titulo, linhas_cabecalho, secoes)


def _linhas(itens: list[dict]) -> list[tuple[str, ...]]:
    return [
        (
            str(item.get("item_order") or "-"),
            item.get("description_invoice") or "",
            item.get("item_name_po") or "",
            fmt(item.get("qty_invoice")),
            fmt(item.get("qty_po")),
            fmt(item.get("price_invoice")),
            fmt(item.get("total_invoice")),
            fmt(item.get("total_po")),
        )
        for item in itens
    ]
