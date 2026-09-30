"""Cabecalhos e reguas do console.

Recolhe o padrao que ja estava repetido em todo o projeto - print("="*60),
titulo, print("="*60) - para que as etapas imprimam do mesmo jeito e o
`main.py` consiga numerar os fluxos.

**Este e o unico modulo que imprime de proposito.** Os `print` daqui (e o
RESUMO de `crawler/reports/execution_report.py`, que usa estas funcoes) sao
APRESENTACAO: a moldura que o operador le no terminal, com regua alinhada e
numeracao de fluxo. Nao sao diagnostico, e por isso nao passam pelo
`logging` - passar tornaria o layout ilegivel (prefixo de data/nivel em cada
regua) e duplicaria tudo no arquivo de log. Mensagem de diagnostico, mesmo
que va para o console, usa `commons/logging_config.get_logger`.
"""
from __future__ import annotations
import sys



# ---------------------------------------------------------------------------
# Versao com blocos Unicode (bonita). Usada quando o console consegue
# representar os caracteres - ver _console_suporta_blocos().
# ---------------------------------------------------------------------------
_BANNER_UNICODE = r"""
        ·  ∘
      ∘  ·         █▀▄ ▄▀█ ▀█▀ ▄▀█
    ·  ∘           █▄▀ █▀█  █  █▀█
   ∘
        ██████  ██    ██ ██    ██ ██
       ██       ██    ██ ██    ██ ██
       ██  ███  ██    ██  ██  ██  ██
       ██   ██  ██    ██   ████   ██
        ██████   ██████     ██    ██
"""

# ---------------------------------------------------------------------------
# Fallback em ASCII puro. O console do Windows na maquina da cliente pode
# estar em cp1252, onde os blocos acima levantam UnicodeEncodeError - e um
# robo que quebra na primeira linha do log e pior que um banner feio.
# ---------------------------------------------------------------------------
_BANNER_ASCII = r"""
        .  o
      o  .         ###   ##  ####  ##
    .  o           #  # #  #  ##  #  #
   o               ###  ####  ##  ####

        ######  ##    ## ##    ## ##
       ##       ##    ## ##    ## ##
       ##  ###  ##    ##  ##  ##  ##
       ##   ##  ##    ##   ####   ##
        ######   ######     ##    ##
"""

LARGURA = 60


def regua(caractere: str = "=", largura: int = LARGURA) -> None:
    print(caractere * largura)


def cabecalho(titulo: str, largura: int = LARGURA) -> None:
    """Abertura de uma execucao ou de uma etapa."""
    print()
    regua("=", largura)
    print(f"  {titulo}")
    regua("=", largura)


def fluxo(numero: int, total: int, titulo: str, largura: int = LARGURA) -> None:
    """Marca uma etapa do pipeline: 'FLUXO 2/3 - COTACAO SEMANAL'."""
    cabecalho(f"FLUXO {numero}/{total} - {titulo.upper()}", largura)


def secao(titulo: str, largura: int = LARGURA) -> None:
    """Subdivisao dentro de uma etapa."""
    print(f"\n--- {titulo} ---")


def resultado(numero: int, total: int, titulo: str, status: str,
              detalhe: str = "") -> None:
    """Uma linha por etapa no fecho do tick: '[2/3] Cotacao ... OK'."""
    sufixo = f"  {detalhe}" if detalhe else ""
    print(f"  [{numero}/{total}] {titulo:<28} {status}{sufixo}")



def _console_suporta_blocos():
    """Diz se o stdout atual consegue codificar os blocos Unicode do banner."""
    encoding = getattr(sys.stdout, "encoding", None) or "ascii"
    try:
        _BANNER_UNICODE.encode(encoding)
        return True
    except (UnicodeEncodeError, LookupError):
        return False


def imprimir_banner():
    """Imprime o logo DataGuvi na abertura da execucao."""
    print("=" * 80)
    print(_BANNER_UNICODE if _console_suporta_blocos() else _BANNER_ASCII)
    print("=" * 80)

