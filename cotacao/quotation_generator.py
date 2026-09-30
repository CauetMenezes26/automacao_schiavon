"""Geração e atualização de planilhas de cotação por fornecedor."""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import NamedTuple

import openpyxl

from domain.model.cotacao import QuotationPrice
from commons.paths import OUTBOUND_DIR
from openpyxl.styles import Font, PatternFill
from openpyxl.utils import get_column_letter
from commons.logging_config import get_logger

log = get_logger(__name__)


_MONTH_NAMES = {
    1: "Janeiro", 2: "Fevereiro", 3: "Marco", 4: "Abril",
    5: "Maio", 6: "Junho", 7: "Julho", 8: "Agosto",
    9: "Setembro", 10: "Outubro", 11: "Novembro", 12: "Dezembro",
}

_BOLD_FONT = Font(bold=True)
_YELLOW_FILL = PatternFill(start_color="FFFFFF00", end_color="FFFFFF00", fill_type="solid")
_LIGHT_YELLOW_FILL = PatternFill(start_color="FFFFFFCC", end_color="FFFFFFCC", fill_type="solid")
_DATE_FORMAT = "mm-dd-yy"

_COL_A_WIDTH = 3.85
_COL_B_WIDTH = 53.43
_DATE_COL_WIDTH = 15.57


def _extract_item_code(item_name: str) -> str | None:
    m = re.search(r"-\s*(\d{4,6})\s*$", item_name.strip())
    return m.group(1) if m else None


def _parse_price(raw) -> tuple[Decimal, str | None]:
    if raw is None:
        return Decimal("0"), None
    raw_str = str(raw).strip()
    if raw_str.upper() in ("N/A", "NA", "-", "", "XX"):
        return Decimal("0"), raw_str or None
    cleaned = raw_str.replace("$", "").replace(",", ".")
    candidates = [p.strip() for p in re.split(r"[/\s]+", cleaned) if p.strip()]
    for c in candidates:
        try:
            return Decimal(c), raw_str
        except InvalidOperation:
            continue
    return Decimal("0"), raw_str


def current_month_sheet_name(ref_date: date | None = None) -> str:
    d = ref_date or date.today()
    return _MONTH_NAMES[d.month]


def _previous_month_sheet_name(ref_date: date) -> str:
    """Nome da aba do mês anterior ao de `ref_date` (mesmo esquema de nomes)."""
    first_of_month = ref_date.replace(day=1)
    last_day_prev_month = first_of_month - timedelta(days=1)
    return _MONTH_NAMES[last_day_prev_month.month]


def _find_last_header_col(ws) -> int:
    """Retorna o índice da última coluna com valor na linha 4 (header).

    Não existe coluna de preço fixa: a partir da C, cada coluna é a data de
    um ciclo — é ali, embaixo da data, que o fornecedor preenche o preço
    daquela semana.
    """
    col = 3  # começa na C (primeira coluna de data)
    while ws.cell(row=4, column=col).value is not None:
        col += 1
    return col - 1


def create_initial_quotation_excel(
    supplier_name: str,
    items: list[dict],
    output_dir: Path = OUTBOUND_DIR,
    ref_date: date | None = None,
) -> Path:
    """
    Cria o Excel inicial de cotação para um fornecedor.

    items: lista de dicts com keys: item_name, item_code, category, sort_order
    Retorna o caminho do arquivo criado.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    file_path = output_dir / f"COTACAO_{supplier_name.upper().replace(' ', '_')}.xlsx"

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = current_month_sheet_name(ref_date)

    ws.column_dimensions["A"].width = _COL_A_WIDTH
    ws.column_dimensions["B"].width = _COL_B_WIDTH

    ws.cell(row=3, column=2, value=f"{supplier_name.upper()} ")

    ws.cell(row=4, column=2, value="ITEM").font = _BOLD_FONT
    # Sem coluna de preço fixa: a primeira coluna de data (C) só entra via
    # add_quotation_column(), que é chamada logo depois desta função em todo
    # call site. É embaixo dela que o fornecedor preenche o preço da semana.

    current_category = None
    row = 5

    sorted_items = sorted(items, key=lambda x: (x.get("sort_order", 0), x.get("item_name", "")))

    for item in sorted_items:
        category = item.get("category", "")

        if current_category is not None and category != current_category:
            row += 1

        current_category = category

        cell_b = ws.cell(row=row, column=2, value=item["item_name"])
        cell_b.font = _BOLD_FONT
        cell_b.fill = _YELLOW_FILL

        row += 1

    wb.save(file_path)
    wb.close()
    log.info("OK: Excel criado: %s (%s itens)", file_path.name, row - 5)
    return file_path


def add_quotation_column(
    file_path: Path,
    quote_date: date,
) -> int:
    """
    Adiciona uma nova coluna de data no Excel existente do fornecedor.

    Retorna o índice da coluna adicionada.
    """
    wb = openpyxl.load_workbook(file_path)

    sheet_name = current_month_sheet_name(quote_date)
    if sheet_name in wb.sheetnames:
        ws = wb[sheet_name]
    else:
        ws = wb.create_sheet(title=sheet_name)
        prev_name = _previous_month_sheet_name(quote_date)
        # Molde é a aba do mês anterior — é ela que reflete o item mais recente
        # que o fornecedor cotou. Cai para a primeira aba do arquivo só no
        # bootstrap, quando ainda não existe "mês anterior" (arquivo novo).
        source = wb[prev_name] if prev_name in wb.sheetnames else wb.worksheets[0]
        _copy_items_to_new_sheet(source, ws)

    # A aba do ciclo em curso é sempre a que deve abrir por padrão — sem isto,
    # criar uma aba nova (linha acima) deixa o Excel abrindo onde estava antes
    # (ex.: a aba do mês da criação do arquivo), sem a coluna que o fornecedor
    # precisa preencher. Foi exatamente esse o bug observado: item preenchido
    # numa aba sem coluna de data nenhuma.
    wb.active = wb.index(ws)

    last_col = _find_last_header_col(ws)
    new_col = last_col + 1

    header_cell = ws.cell(row=4, column=new_col, value=datetime(quote_date.year, quote_date.month, quote_date.day))
    header_cell.font = _BOLD_FONT
    header_cell.number_format = _DATE_FORMAT

    ws.column_dimensions[get_column_letter(new_col)].width = _DATE_COL_WIDTH

    row = 5
    while row <= ws.max_row:
        item_val = ws.cell(row=row, column=2).value
        if item_val and str(item_val).strip():
            cell = ws.cell(row=row, column=new_col)
            cell.fill = _LIGHT_YELLOW_FILL
        row += 1

    wb.save(file_path)
    wb.close()

    supplier = ws.cell(row=3, column=2).value or file_path.stem
    log.info(
        "OK: Coluna %s (%s) adicionada em %s",
        get_column_letter(new_col), quote_date.strftime('%d/%m/%Y'), file_path.name,
    )
    return new_col


def _copy_items_to_new_sheet(source, dest):
    """Copia a estrutura de itens (colunas A-B) de uma sheet para outra.

    Só A-B: a partir da C é preço por semana, não molde. Copiar a C traria o
    preço da primeira semana do mês anterior parar dentro do header da aba
    nova, disfarçado de coluna de data.
    """
    dest.column_dimensions["A"].width = _COL_A_WIDTH
    dest.column_dimensions["B"].width = _COL_B_WIDTH

    for row in range(3, source.max_row + 1):
        for col in range(1, 3):
            src_cell = source.cell(row=row, column=col)
            dst_cell = dest.cell(row=row, column=col, value=src_cell.value)
            if src_cell.font:
                dst_cell.font = src_cell.font.copy()
            if src_cell.fill and src_cell.fill.fill_type:
                dst_cell.fill = src_cell.fill.copy()


def read_quotation_column(
    file_path: Path,
    column_index: int,
    sheet_name: str | None = None,
) -> list[QuotationPrice]:
    """
    Lê os preços preenchidos pelo fornecedor na coluna especificada.

    Retorna lista de QuotationPrice com os itens que têm preço preenchido.
    """
    wb = openpyxl.load_workbook(file_path, data_only=True)

    if sheet_name:
        ws = wb[sheet_name]
    else:
        ws = wb.active

    results: list[QuotationPrice] = []
    row = 5

    while row <= ws.max_row:
        item_val = ws.cell(row=row, column=2).value
        if not item_val or not str(item_val).strip():
            row += 1
            continue

        item_name = str(item_val).strip()
        item_code = _extract_item_code(item_name)
        raw_price = ws.cell(row=row, column=column_index).value
        price, price_raw = _parse_price(raw_price)

        results.append(QuotationPrice(
            item_name=item_name,
            item_code=item_code,
            price=price,
            price_raw=price_raw,
            row_index=row,
        ))
        row += 1

    wb.close()
    return results


def has_responses(prices: list[QuotationPrice]) -> bool:
    """Verifica se pelo menos um item tem preço preenchido (> 0)."""
    return any(p.price > 0 for p in prices)


def get_supplier_name(file_path: Path) -> str:
    """Extrai o nome do fornecedor da planilha (linha 3, coluna B)."""
    wb = openpyxl.load_workbook(file_path, data_only=True)
    ws = wb.active
    name = ws.cell(row=3, column=2).value
    wb.close()
    return str(name).strip() if name else file_path.stem
