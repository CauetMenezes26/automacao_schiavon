"""Configuração de log padrão do RPA.

Equivalente Python do `log4j2.xml` + `.cursor/rules/logs-rpa.mdc` do rpa-modelo.
Hoje o projeto usa `print()` com `✓ ✗ ⚠` espalhado; a Fase 5 troca isso por
`logging`. Este módulo já deixa o padrão pronto.

Chamada única, no bootstrap (`main.py` / `crawler/controller.py`):

    from commons.logging_config import configurar_logs
    configurar_logs()                          # console, nível INFO
    configurar_logs("DEBUG", ARQUIVO_LOG)      # console + arquivo rotativo

Nos demais módulos:

    from commons.logging_config import get_logger
    log = get_logger(__name__)
    log.info("Coleta iniciada - configs: %d", len(configs))

Regras (ver `.cursor/rules/logs-rpa.mdc`):

* Níveis: INFO fluxo normal · DEBUG detalhe de troubleshooting · WARNING
  situação anômala recuperável (retry, dado inconsistente, BusinessException) ·
  ERROR falha que impacta o processamento.
* **Sem emoji e sem acentuação decorativa** — texto ASCII puro. Emoji quebra
  parsing de log e encoding em terminal legado.
* Placeholders `%s`/`%d`, nunca f-string ou concatenação: adia a formatação
  para quando o nível está de fato habilitado.
* Em `except`, use `log.exception("contexto - id: %s", id)` (registra a stack)
  ou `log.error("...", exc_info=True)`. Nunca engula a exceção só com `pass`.
* Log por camada: Controller só início/fim/erro geral · Flow etapas e métricas
  · Service operações de dado e validação · Utils detalhe técnico de baixo
  nível. Evite log dentro de loop item a item — logue a cada N ou só o resumo.
"""

from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

__all__ = ["configurar_logs", "get_logger"]

_FORMATO = "%(asctime)s %(levelname)-5s %(name)s | %(message)s"
_DATA = "%Y-%m-%d %H:%M:%S"
_configurado = False


def configurar_logs(
    nivel: str | int = "INFO",
    arquivo: str | Path | None = None,
    *,
    max_bytes: int = 5 * 1024 * 1024,
    backups: int = 5,
) -> None:
    """Instala o handler de console (e, se `arquivo`, um handler rotativo).

    Idempotente: chamar de novo só ajusta o nível, não duplica handler.
    Silencia o ruído de bibliotecas de terceiros abaixo de WARNING.
    """
    global _configurado

    root = logging.getLogger()
    root.setLevel(nivel)

    if not _configurado:
        console = logging.StreamHandler(sys.stdout)
        console.setFormatter(logging.Formatter(_FORMATO, _DATA))
        root.addHandler(console)

        if arquivo is not None:
            caminho = Path(arquivo)
            caminho.parent.mkdir(parents=True, exist_ok=True)
            rotativo = RotatingFileHandler(
                caminho, maxBytes=max_bytes, backupCount=backups, encoding="utf-8"
            )
            rotativo.setFormatter(logging.Formatter(_FORMATO, _DATA))
            root.addHandler(rotativo)

        for ruidoso in ("httpx", "httpcore", "anthropic", "playwright", "urllib3", "PIL"):
            logging.getLogger(ruidoso).setLevel(logging.WARNING)

        _configurado = True


def get_logger(nome: str) -> logging.Logger:
    """Logger nomeado pelo módulo. Use `get_logger(__name__)`."""
    return logging.getLogger(nome)
