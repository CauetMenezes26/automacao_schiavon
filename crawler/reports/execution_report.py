"""Relatório de execução do pipeline.

Consolida o desfecho de cada fluxo de uma passada de `python main.py` e emite:

  * o RESUMO no console (mesmo layout de antes, via `commons/banner.py`);
  * `execution_report.json` na raiz — para o cron / dashboard ler o estado da
    última execução sem abrir log.

O `Pipeline` (`crawler/pipeline.py`) alimenta um `ExecutionReport` a cada
`rodar()` e chama `emitir()` no fim. Não confundir com o `resultado.json` que o
FLUXO 2 grava — aquele é o detalhe da coleta de invoices; este é o placar da
execução inteira.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from commons.banner import cabecalho, regua, resultado
from commons.logging_config import get_logger
from commons.paths import ROOT

log = get_logger(__name__)

_JSON_PADRAO = ROOT / "execution_report.json"


@dataclass
class FluxoResultado:
    numero: int
    titulo: str
    estado: str            # "OK" | "ERRO"
    duracao_s: float


@dataclass
class ExecutionReport:
    total: int
    iniciado_em: datetime = field(default_factory=datetime.now)
    fluxos: list[FluxoResultado] = field(default_factory=list)

    def registrar(self, numero: int, titulo: str, estado: str, duracao_s: float) -> None:
        self.fluxos.append(FluxoResultado(numero, titulo, estado, duracao_s))

    @property
    def houve_erro(self) -> bool:
        return any(f.estado == "ERRO" for f in self.fluxos)

    def emitir_console(self) -> None:
        cabecalho("RESUMO")
        for f in self.fluxos:
            resultado(f.numero, self.total, f.titulo, f.estado)
        regua()

    def gravar_json(self, caminho: str | Path | None = None) -> Path:
        destino = Path(caminho) if caminho is not None else _JSON_PADRAO
        payload = {
            "executado_em": self.iniciado_em.isoformat(timespec="seconds"),
            "duracao_s": round((datetime.now() - self.iniciado_em).total_seconds(), 1),
            "houve_erro": self.houve_erro,
            "fluxos": [
                {"n": f.numero, "titulo": f.titulo, "estado": f.estado,
                 "duracao_s": round(f.duracao_s, 1)}
                for f in self.fluxos
            ],
        }
        destino.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        return destino

    def emitir(self) -> None:
        """RESUMO no console + `execution_report.json`. Best-effort no arquivo."""
        self.emitir_console()
        try:
            destino = self.gravar_json()
            log.info("relatorio de execucao gravado em %s", destino.name)
        except OSError as exc:
            log.warning("relatorio de execucao nao gravado: %s", exc)
