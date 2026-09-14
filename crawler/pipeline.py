"""Orquestrador do pipeline — roda os fluxos em sequência, isolando falhas.

O `main.py` só monta o `Pipeline` e chama `rodar()` uma vez por fluxo. Toda a
mecânica de "uma falha num fluxo não derruba os outros", o RESUMO no fim e o
código de saída ficam aqui, fora do `main`. O placar por fluxo mora num
`ExecutionReport` (`crawler/reports/execution_report.py`).
"""

from __future__ import annotations

import sys
import time
import traceback
from typing import Callable

from commons.banner import fluxo
from crawler.reports.execution_report import ExecutionReport

__all__ = ["Pipeline"]


class Pipeline:
    """Sequência de fluxos com isolamento de falha.

        pipe = Pipeline(total=5)
        pipe.rodar(1, "Sinonimos", lambda: sinonimos_flow(env))
        pipe.rodar(2, "Invoices",  lambda: invoices_flow(env))
        ...
        pipe.resumo()          # RESUMO + execution_report.json; sai com 1 se algo falhou
    """

    def __init__(self, total: int) -> None:
        self.total = total
        self.report = ExecutionReport(total)

    @property
    def houve_erro(self) -> bool:
        """True se algum fluxo terminou em ERRO. O `controller` lê isto para o
        heartbeat, antes de `resumo()` sair com código 1."""
        return self.report.houve_erro

    def rodar(self, numero: int, titulo: str, acao: Callable[[], object]) -> str:
        """Executa um fluxo. Registra 'OK' ou 'ERRO' e nunca propaga a exceção."""
        fluxo(numero, self.total, titulo)
        inicio = time.perf_counter()
        try:
            acao()
            estado = "OK"
        except Exception as exc:  # noqa: BLE001 — um fluxo não pode derrubar os demais
            traceback.print_exc()
            print(f"\n  ✗ FLUXO {numero}/{self.total} falhou: {exc}")
            estado = "ERRO"
        self.report.registrar(numero, titulo, estado, time.perf_counter() - inicio)
        return estado

    def resumo(self) -> None:
        """Fecha o tick: emite o RESUMO + `execution_report.json` e sai com
        código 1 se algum fluxo falhou."""
        self.report.emitir()
        if self.report.houve_erro:
            sys.exit(1)
