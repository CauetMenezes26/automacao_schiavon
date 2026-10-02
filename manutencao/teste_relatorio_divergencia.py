"""Gera um relatorio de divergencia FAKE em `DIVERGENCIAS_ERP_DIR`, para inspecao visual.

Sem banco nem Catapult: `header` e `resultado` sao dicts fake no formato de
`reconcile_items_against_po`. Tem um item ok, um com divergencia de quantidade,
um de valor e um com os dois, para exercitar as duas secoes.

    python -m manutencao.teste_relatorio_divergencia
"""

from __future__ import annotations

from decimal import Decimal

from conciliacao.relatorio_divergencia import gerar_relatorio_divergencia_erp
from domain.conciliacao_codes import IssueCode

HEADER = {
    "id": 999998,
    "invoice_number": "FAKE-2026-002",
    "supplier_name": "Fake Supplier LLC",
    "invoice_date": "2026-09-30",
    "id_loja": 2,
}


def _item(ordem: int, descricao: str, po: str, qtd_inv: str, qtd_po: str,
          preco: str, total_po: str, codes: list) -> dict:
    qtd_inv, qtd_po, preco, total_po = map(Decimal, (qtd_inv, qtd_po, preco, total_po))
    total_inv = qtd_inv * preco
    return {
        "item_order": ordem, "description_invoice": descricao, "item_name_po": po,
        "qty_invoice": qtd_inv, "qty_po": qtd_po, "qty_diff": qtd_inv - qtd_po,
        "price_invoice": preco, "total_invoice": total_inv, "total_po": total_po,
        "price_diff": total_inv - total_po,
        "issue_codes": codes, "has_issue": bool(codes), "needs_review": False,
    }


ITENS = [
    _item(1, "BEEF - Sirloin Top Butt", "Beef Sirloin Top Butt", "10", "10", "125.60", "1256.00", []),
    _item(2, "CHICKEN BREAST BONELESS", "Chicken Breast Boneless 40lb", "4", "3", "62.30", "186.90",
          [IssueCode.QTY_MISMATCH_PO]),
    _item(3, "OLIVE OIL EXTRA VIRGIN", "Olive Oil EV 3L", "6", "6", "28.75", "162.00",
          [IssueCode.PRICE_MISMATCH_PO]),
    _item(4, "TOMATO PASTE 6/10", "Tomato Paste 6/10", "5", "4", "40.00", "150.00",
          [IssueCode.QTY_MISMATCH_PO, IssueCode.PRICE_MISMATCH_PO]),
]

RESULTADO = {
    "items": ITENS, "issue_codes": [], "has_issue": True,
    "needs_review": False, "po_orphans": [],
}


def main() -> None:
    caminho = gerar_relatorio_divergencia_erp(HEADER, RESULTADO)
    print(f"Salvo em: {caminho}")


if __name__ == "__main__":
    main()
