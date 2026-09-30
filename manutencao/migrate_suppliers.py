"""Migração única: popula `dim_fornecedor` a partir do Excel de cadastro.

Uso (a partir da raiz do projeto):
    python -m manutencao.migrate_suppliers

A importação de itens de cotação foi removida: `quotation_items` nunca chegou
a existir no banco, e o dwschiavon2 não tem dimensão de item por decisão de
projeto — o código do item nem sempre vem preenchido na planilha, então o
casamento é por texto.
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path

from domain.service.cotacao_service import save_meat_supplier
from commons.db import connect_db
from domain.config import carregar_config

from commons.paths import READ_DIR  # noqa: E402

SUPPLIER_EXCEL_PATH = Path(
    r"C:\Users\Cauet\OneDrive\Documents\Fornecedores_Automação_Schiavon.xlsx"
)

_CATEGORY_ORDER = {"BEEF": 100, "PORK": 200, "CHICKEN": 300, "OTHERS": 400}


def import_suppliers_from_excel(conn) -> int:
    """
    Importa fornecedores do Excel de cadastro para a tabela meat_suppliers.
    """
    import openpyxl

    if not SUPPLIER_EXCEL_PATH.exists():
        print(f"Planilha não encontrada: {SUPPLIER_EXCEL_PATH}")
        return 0

    wb = openpyxl.load_workbook(SUPPLIER_EXCEL_PATH, data_only=True)
    ws = wb.active

    headers = [str(ws.cell(row=1, column=c).value or "").lower() for c in range(1, ws.max_column + 1)]
    print(f"Colunas encontradas: {headers}")

    col_map = {}
    for idx, h in enumerate(headers, 1):
        if "fornecedor" in h and "ativo" not in h:
            col_map["name"] = idx
        elif "vendedor" in h:
            col_map["contact_name"] = idx
        elif "mail" in h:
            col_map["email"] = idx
        elif "whatsapp" in h:
            col_map["whatsapp"] = idx
        elif "enviar" in h or "onde" in h:
            col_map["channel_raw"] = idx
        elif "ativo" in h:
            col_map["active_raw"] = idx

    inserted = 0
    for row in range(2, ws.max_row + 1):
        name = ws.cell(row=row, column=col_map.get("name", 1)).value
        if not name or not str(name).strip():
            continue

        contact = ws.cell(row=row, column=col_map.get("contact_name", 2)).value
        email = ws.cell(row=row, column=col_map.get("email", 3)).value
        whatsapp = ws.cell(row=row, column=col_map.get("whatsapp", 4)).value
        channel_raw = ws.cell(row=row, column=col_map.get("channel_raw", 5)).value
        active_raw = ws.cell(row=row, column=col_map.get("active_raw", 6)).value

        if active_raw and str(active_raw).lower().startswith("n"):
            continue

        channel = "whatsapp"
        if channel_raw:
            cr = str(channel_raw).lower()
            if "email" in cr and "whatsapp" in cr:
                channel = "both"
            elif "email" in cr:
                channel = "email"

        whatsapp_clean = None
        if whatsapp:
            whatsapp_clean = "".join(filter(str.isdigit, str(whatsapp)))

        supplier_id = save_meat_supplier(
            conn,
            name=str(name).strip(),
            contact_name=str(contact).strip() if contact else None,
            email=str(email).strip() if email else None,
            whatsapp=whatsapp_clean,
            channel=channel,
        )
        print(f"  ✓ {name} (id={supplier_id}, canal={channel})")
        inserted += 1

    wb.close()
    print(f"\n✓ {inserted} fornecedor(es) inserido(s) em meat_suppliers.")
    return inserted


def main() -> None:
    argparse.ArgumentParser(
        description="Importa fornecedores do Excel de cadastro para dim_fornecedor."
    ).parse_args()

    config = carregar_config()
    conn = connect_db(config.banco)
    try:
        n = import_suppliers_from_excel(conn)
        print()
        print(f"{n} fornecedor(es) importado(s).")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
