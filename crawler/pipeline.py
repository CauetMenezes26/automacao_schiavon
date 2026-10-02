from __future__ import annotations

import sys
import time
from typing import Callable

from commons.banner import fluxo
from commons.logging_config import get_logger
from crawler.reports.execution_report import ExecutionReport
from domain.service import notificacao_service

__all__ = ["Pipeline"]

log = get_logger(__name__)


class Pipeline:

    def __init__(self, total: int) -> None:
        self.total = total
        self.report = ExecutionReport(total)

    @property
    def houve_erro(self) -> bool:
        return self.report.houve_erro

    def rodar(self, numero: int, titulo: str, acao: Callable[[], object]) -> str:
        fluxo(numero, self.total, titulo)
        inicio = time.perf_counter()
        try:
            acao()
            estado = "OK"
        except Exception as exc:
            log.exception("FLUXO %s/%s (%s) falhou: %s", numero, self.total, titulo, exc)
            notificacao_service.registrar_erro(f"FLUXO {numero}/{self.total} {titulo}", exc)
            estado = "ERRO"
        self.report.registrar(numero, titulo, estado, time.perf_counter() - inicio)
        return estado

    def resumo(self) -> None:
        self.report.emitir()
        if self.report.houve_erro:
            sys.exit(1)
