"""Gera um relatorio de sucesso FAKE em `SUCESSOS_ERP_DIR`, para inspecao visual.

Sem banco nem Catapult: `header` e `resultado` sao dicts fake no formato de
`reconcile_items_against_po`.

    python -m manutencao.teste_relatorio_sucesso
"""

from __future__ import annotations

from decimal import Decimal

from conciliacao.relatorio_sucesso import gerar_relatorio_sucesso_erp

HEADER = {
    "id": 999999,
    "invoice_number": "FAKE-2026-001",
    "supplier_name": "Fake Supplier LLC",
    "invoice_date": "2026-09-30",
    "id_loja": 1,
}


def _item(ordem: int, descricao: str, po: str, qtd: str, preco: str) -> dict:
    total = Decimal(qtd) * Decimal(preco)
    return {
        "item_order": ordem, "description_invoice": descricao, "item_name_po": po,
        "qty_invoice": Decimal(qtd), "qty_po": Decimal(qtd),
        "price_invoice": Decimal(preco), "total_invoice": total, "total_po": total,
        "issue_codes": [], "has_issue": False, "needs_review": False,
    }


ITENS = [
    _item(1, "BEEF - Sirloin Top Butt", "Beef Sirloin Top Butt", "10", "125.60"),
    _item(2, "CHICKEN BREAST BONELESS", "Chicken Breast Boneless 40lb", "4", "62.30"),
    _item(3, "OLIVE OIL EXTRA VIRGIN", "Olive Oil EV 3L", "6", "28.75"),
]

RESULTADO = {
    "items": ITENS, "issue_codes": [], "has_issue": False,
    "needs_review": False, "po_orphans": [],
}


def main() -> None:
    caminho = gerar_relatorio_sucesso_erp(HEADER, RESULTADO)
    print(f"Salvo em: {caminho}")


if __name__ == "__main__":
    main()
