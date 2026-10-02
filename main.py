from __future__ import annotations
from crawler.controller import executar
from commons.datas import fixar_fuso
from commons.logging_config import configurar_logs, get_logger
import sys

log = get_logger(__name__)

if __name__ == "__main__":
    fixar_fuso()
    configurar_logs()
    try:
        executar()
    except SystemExit:
        raise
    except Exception:
        log.exception("execucao abortada antes dos fluxos")
        sys.exit(1)
