"""Tipos de alerta à operação — a fonte da verdade, em código.

Mesmo arranjo de `categorias.py` e `status_exec.py`: o banco (`alerta.tipo`)
guarda o valor, o significado e a severidade moram aqui.

POR QUE ESTE MÓDULO EXISTE

Até aqui não havia canal nenhum de aviso a humanos: uma falha de login sob cron
era invisível. `alerta` é um ledger com dedupe (índice único parcial
`WHERE resolvido_em IS NULL`) — no máximo um alerta aberto por `(tipo,
chave_dedupe)`, então o e-mail para a GUVI sai uma vez, não a cada execução.

`chave()` monta a `chave_dedupe`. Inclui um balde temporal quando o alerta é
recorrente por natureza (pipeline parado), para reabrir no máximo uma vez por
dia em vez de a cada tick.
"""

from __future__ import annotations

from datetime import date
from enum import StrEnum

__all__ = ["TipoAlerta", "SEVERIDADE", "chave"]


class TipoAlerta(StrEnum):
    """Gravado em `alerta.tipo`."""

    LOGIN_FALHA = "login_falha"          # autenticação falhou num sistema
    PIPELINE_PARADO = "pipeline_parado"  # nenhuma execução há mais de X horas
    LEITURA_PARADA = "leitura_parada"    # coleta/leitura de invoice parada há X horas

    @property
    def descricao(self) -> str:
        return _DESCRICAO[self]


_DESCRICAO: dict[TipoAlerta, str] = {
    TipoAlerta.LOGIN_FALHA: "Falha de autenticação num sistema do fluxo.",
    TipoAlerta.PIPELINE_PARADO: "Pipeline sem execução há mais que o limite.",
    TipoAlerta.LEITURA_PARADA: "Coleta/leitura de invoice parada há mais que o limite.",
}

SEVERIDADE: dict[TipoAlerta, str] = {
    TipoAlerta.LOGIN_FALHA: "erro",
    TipoAlerta.PIPELINE_PARADO: "erro",
    TipoAlerta.LEITURA_PARADA: "alerta",
}

# Tipos cuja recorrência é esperada: a chave ganha um balde de data para reabrir
# no máximo 1x/dia.
_COM_BALDE_DIARIO = {TipoAlerta.PIPELINE_PARADO, TipoAlerta.LEITURA_PARADA}


def chave(tipo: TipoAlerta, origem: str | None = None) -> str:
    """Monta a `chave_dedupe` estável de um alerta."""
    partes = [str(tipo)]
    if origem:
        partes.append(origem)
    if tipo in _COM_BALDE_DIARIO:
        partes.append(date.today().isoformat())
    return ":".join(partes)
