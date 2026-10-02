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

import os
import time
from datetime import date, timedelta

__all__ = ["week_bounds", "fixar_fuso", "FUSO_OPERACAO"]

FUSO_OPERACAO = "America/Sao_Paulo"


def fixar_fuso() -> None:
    """Fixa o fuso do processo em America/Sao_Paulo (o servidor roda em UTC).

    Faz `datetime.now()`, `date.today()`, o horario dos logs e o `croniter`
    seguirem Sao Paulo, como o banco (que grava `AT TIME ZONE
    'America/Sao_Paulo'`). Chamar uma vez, no inicio de `main.py`. Em Windows
    nao existe `time.tzset`: nao faz nada.
    """
    os.environ["TZ"] = FUSO_OPERACAO
    if hasattr(time, "tzset"):
        time.tzset()


def week_bounds(reference: date) -> tuple[date, date]:
    """Segunda e domingo da semana da data informada.

    Semana da operação é segunda a domingo (não domingo a sábado): é o
    recorte que o ciclo de cotação e a conciliação já usavam.
    """
    inicio = reference - timedelta(days=reference.weekday())
    return inicio, inicio + timedelta(days=6)
