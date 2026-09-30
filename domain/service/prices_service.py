"""Leitura e importação de cotações de preço (Excel) no banco."""

from __future__ import annotations

import re
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

import openpyxl
import psycopg2
import psycopg2.extensions

from commons.paths import PRICE_QUOTE_DIR, READ_DIR
from domain.model.cotacao import PriceRow
from domain.service.cotacao_service import fetch_processo_do_ciclo
from domain.service.processo_service import SCHEMA
from commons.logging_config import get_logger

log = get_logger(__name__)


# ---------------------------------------------------------------------------
# Destino: dwschiavon2.fat_cotacao_preco
#
#   id_ciclo       <- PriceRow.id_request
#   id_fornecedor  <- PriceRow.id_supplier
#   item_nome / item_codigo / preco / preco_raw / comentario
#
# `supplier` (texto) e `file_name` nao existem mais: o fornecedor e FK e o
# arquivo vive em fat_cotacao_envio. Guardar o nome solto era o que permitia
# uma linha de preco existir sem dono.
# ---------------------------------------------------------------------------


# Nomes de aba que o Excel cria sozinho e não identificam fornecedor nenhum.
# Quando a aba tem um desses nomes, o fornecedor vem do nome do arquivo.
_GENERIC_SHEETS = {"PLANILHA1", "PLANILHA", "SHEET1", "SHEET", "PLAN1", "FOLHA1"}

# Rótulos da linha de cabeçalho da planilha, que não são itens.
_HEADER_LABELS = {"ITEM", "PRICE", "COMMENTS", "PRECO", "PRODUTO"}


def _clean_cell(value) -> str:
    """Texto da célula sem espaço não-separável e sem espaços sobrando.

    O \\xa0 vem do Excel e, se não for tratado, entra no banco e atrapalha
    qualquer comparação de texto depois.
    """
    return str(value).replace("\xa0", " ").strip() if value is not None else ""


def _supplier_from_file_name(file_name: str) -> str:
    """Deriva o fornecedor do nome do arquivo.

    'CHENEY.xlsx'                    -> 'CHENEY'
    'COTACAO_DBA_MEATS.xlsx'         -> 'DBA MEATS'
    '20260623_170310_CHENEY.xlsx'    -> 'CHENEY'

    Inverte a convenção de quotation._file_name_for_supplier(). O prefixo de
    data/hora é o que import_price_file() carimba ao mover o arquivo para
    files/read_files/.
    """
    stem = Path(file_name).stem
    stem = re.sub(r"^\d{8}_\d{6}_", "", stem)
    if stem.upper().startswith("COTACAO_"):
        stem = stem[len("COTACAO_"):]
    return stem.replace("_", " ").strip()


def _extract_item_code(item_name: str) -> str | None:
    """Extrai o código numérico ao final do nome do item (ex: '40122')."""
    m = re.search(r"-\s*(\d{4,6})\s*$", _clean_cell(item_name))
    return m.group(1) if m else None


def _parse_price(raw) -> tuple[Decimal, str | None]:
    """
    Retorna (price, price_raw).

    - None / vazio / 'N/A'     → (0, None ou texto original)
    - '$5,38'                  → (Decimal('5.38'), '$5,38')
    - '$4.95/5.35/$5.41'      → (Decimal('4.95'), '$4.95/5.35/$5.41')  — pega o primeiro
    """
    if raw is None:
        return Decimal("0"), None

    raw_str = str(raw).strip()
    if raw_str.upper() in ("N/A", "NA", "-", ""):
        return Decimal("0"), raw_str or None

    # Remove '$' e normaliza separador decimal (vírgula → ponto)
    cleaned = raw_str.replace("$", "").replace(",", ".")

    # Divide em múltiplos valores (ex: "4.95/5.35/5.41")
    candidates = [p.strip() for p in re.split(r"[/\s]+", cleaned) if p.strip()]

    for c in candidates:
        try:
            return Decimal(c), raw_str
        except InvalidOperation:
            continue

    return Decimal("0"), raw_str


def _iter_sheet_rows(file_path: Path):
    """
    Gera (sheet_name, [(item_raw, price_raw, comments_raw), ...]) para cada aba.
    Suporta .xlsx (openpyxl) e .xls (xlrd).
    """
    if file_path.suffix.lower() == ".xls":
        import xlrd
        wb = xlrd.open_workbook(str(file_path))
        for sheet_name in wb.sheet_names():
            ws = wb.sheet_by_name(sheet_name)
            sheet_rows = []
            for row_idx in range(3, ws.nrows):  # linha 4 = índice 3
                row = ws.row_values(row_idx)
                # colunas B=1, C=2, D=3
                item_raw      = row[1] if len(row) > 1 else None
                price_raw_val = row[2] if len(row) > 2 else None
                comments_raw  = row[3] if len(row) > 3 else None
                sheet_rows.append((item_raw, price_raw_val, comments_raw))
            yield sheet_name, sheet_rows
    else:
        wb = openpyxl.load_workbook(file_path, read_only=True, data_only=True)
        for sheet_name in wb.sheetnames:
            ws = wb[sheet_name]
            sheet_rows = list(
                ws.iter_rows(min_row=4, min_col=2, max_col=4, values_only=True)
            )
            yield sheet_name, sheet_rows
        wb.close()


def read_price_excel(file_path: Path) -> list[PriceRow]:
    """
    Lê todas as abas do Excel; cada aba = um fornecedor.
    Inicia na linha 4; colunas B=ITEM, C=PRICE, D=COMMENTS.
    Suporta qualquer nome de arquivo .xlsx ou .xls.

    Se a aba tiver nome genérico ('Planilha1'), o fornecedor vem do nome do
    arquivo — senão o campo supplier grava 'Planilha1' e fica inútil, que é o
    que aconteceu com as importações antigas.
    """
    rows: list[PriceRow] = []
    fallback_supplier = _supplier_from_file_name(file_path.name)

    for sheet_name, sheet_rows in _iter_sheet_rows(file_path):
        supplier = sheet_name.strip()
        if supplier.upper() in _GENERIC_SHEETS:
            supplier = fallback_supplier

        for item_raw, price_raw_val, comments_raw in sheet_rows:
            item_name = _clean_cell(item_raw)
            if not item_name:
                continue
            # A linha 4 é o cabeçalho em algumas planilhas; sem isto ela entra
            # no banco como se fosse um item chamado 'ITEM'.
            if item_name.upper() in _HEADER_LABELS:
                continue

            item_code = _extract_item_code(item_name)
            price, price_raw = _parse_price(price_raw_val)
            comments = _clean_cell(comments_raw) or None

            rows.append(PriceRow(
                supplier=supplier,
                item_code=item_code,
                item_name=item_name,
                price=price,
                price_raw=price_raw,
                comments=comments,
                file_name=file_path.name,
            ))

    return rows


def save_price_quotes(
    conn: psycopg2.extensions.connection,
    rows: list[PriceRow],
) -> int:
    """Grava os precos cotados. Retorna a quantidade de linhas inseridas.

    **Idempotente por (ciclo, fornecedor):** apaga o que ja havia antes de
    inserir. Sem isso, reimportar a mesma planilha duplicaria os precos, e o
    grao do processo de cotacao e o ciclo — reprocessar significa reprocessar
    a semana inteira.
    """
    if not rows:
        return 0

    # No schema novo um preco SEM ciclo nao existe: id_ciclo e id_fornecedor sao
    # NOT NULL. E de proposito — no dwschiavon havia 106 linhas de price_quote
    # sem id_request, invisiveis para qualquer conciliacao. Falhar aqui, com o
    # motivo, e melhor que um erro de constraint no meio do INSERT.
    orfas = [r for r in rows if r.id_request is None or r.id_supplier is None]
    if orfas:
        raise ValueError(
            f"{len(orfas)} linha(s) de preco sem ciclo ou sem fornecedor "
            f"(ex.: {orfas[0].item_name!r}). Toda cotacao precisa pertencer a um "
            "ciclo: use o fluxo `--so-cotacao`, que passa id_request e id_supplier."
        )

    alvos = {(r.id_request, r.id_supplier) for r in rows
             if r.id_request is not None and r.id_supplier is not None}
    id_processo_por_ciclo = {
        id_ciclo: fetch_processo_do_ciclo(conn, id_ciclo)
        for id_ciclo in {id_ciclo for id_ciclo, _ in alvos}
    }

    with conn.cursor() as cur:
        for id_ciclo, id_forn in alvos:
            cur.execute(
                f"DELETE FROM {SCHEMA}.fat_cotacao_preco"
                " WHERE id_ciclo = %s AND id_fornecedor = %s",
                (id_ciclo, id_forn),
            )

        cur.executemany(
            f"""
            INSERT INTO {SCHEMA}.fat_cotacao_preco
                (id_ciclo, id_fornecedor, id_processo, item_nome, item_codigo,
                 preco, preco_raw, comentario)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """,
            [(r.id_request, r.id_supplier, id_processo_por_ciclo.get(r.id_request),
              r.item_name, r.item_code, float(r.price), r.price_raw, r.comments)
             for r in rows],
        )
    conn.commit()
    return len(rows)


def import_price_file(
    file_path: Path,
    conn: psycopg2.extensions.connection,
) -> int:
    """
    Pipeline completo: lê o Excel e persiste no banco.
    Retorna a quantidade de linhas importadas.
    """
    log.info("Lendo: %s", file_path.name)
    rows = read_price_excel(file_path)

    suppliers = {r.supplier for r in rows}
    log.info(
        "%s linha(s) em %s aba(s): %s",
        len(rows), len(suppliers), ', '.join(sorted(suppliers)),
    )

    inserted = save_price_quotes(conn, rows)
    log.info("%s linha(s) gravada(s) em price_quote", inserted)

    READ_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    dest = READ_DIR / f"{timestamp}_{file_path.name}"
    file_path.rename(dest)
    log.info("-> files/read_files/%s", dest.name)

    return inserted


def import_all_price_files(
    conn: psycopg2.extensions.connection,
    directory: Path = PRICE_QUOTE_DIR,
) -> int:
    """
    Importa todos os arquivos Excel (.xlsx / .xls) encontrados em files/price_quote.
    O nome do arquivo não importa — qualquer arquivo colocado na pasta é processado.
    Retorna o total de linhas inseridas.
    """
    files = sorted(
        f for f in directory.iterdir()
        if f.is_file() and f.suffix.lower() in (".xlsx", ".xls")
    )
    if not files:
        log.info("Nenhum arquivo Excel encontrado em %s", directory)
        return 0

    log.info("Price Quotes - %s arquivo(s) em %s/", len(files), directory.name)

    total = 0
    for file_path in files:
        try:
            total += import_price_file(file_path, conn)
        except Exception as exc:
            log.error("Erro em %s: %s", file_path.name, exc)

    log.info("Total importado: %s linha(s)", total)
    return total
