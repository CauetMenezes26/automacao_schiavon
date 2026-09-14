"""Linhas da cotação — o que é lido das planilhas dos fornecedores."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import NamedTuple


class PriceRow(NamedTuple):
    """Uma linha pronta para gravar em price_quote."""

    supplier:    str
    item_code:   str | None
    item_name:   str
    price:       Decimal
    price_raw:   str | None
    comments:    str | None
    file_name:   str
    # Vínculo com o ciclo semanal. Default None para não quebrar a importação
    # manual (manutencao/importar_precos.py) nem os scripts de migração, que
    # rodam fora de um ciclo.
    id_request:  int | None = None
    id_supplier: int | None = None
    quote_date:  date | None = None


class QuotationPrice(NamedTuple):
    """Um preço lido de uma coluna de semana da planilha."""

    item_name: str
    item_code: str | None
    price:     Decimal
    price_raw: str | None
    row_index: int
