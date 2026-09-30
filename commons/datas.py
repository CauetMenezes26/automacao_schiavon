"""Recorte de datas do projeto — a semana operacional, num lugar só.

`week_bounds` estava implementada duas vezes, igual, em
`conciliacao/reconcile_quote.py` e `cotacao/quotation.py`, e dois fluxos
(`conciliacao_flow`, `reconcile_erp_flow`) importavam a versão PRIVADA de
`reconcile_quote` — um `_`-prefixado atravessando a fronteira do módulo, e
ainda por cima do módulo da conciliação por cotação, que está desativada.
Helper puro e genérico compartilhado por mais de um fluxo desce para
`commons`, e é isso aqui.
"""

from __future__ import annotations

from datetime import date, timedelta

__all__ = ["week_bounds"]


def week_bounds(reference: date) -> tuple[date, date]:
    """Segunda e domingo da semana da data informada.

    Semana da operação é segunda a domingo (não domingo a sábado): é o
    recorte que o ciclo de cotação e a conciliação já usavam.
    """
    inicio = reference - timedelta(days=reference.weekday())
    return inicio, inicio + timedelta(days=6)
