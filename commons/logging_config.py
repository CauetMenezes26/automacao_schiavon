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
