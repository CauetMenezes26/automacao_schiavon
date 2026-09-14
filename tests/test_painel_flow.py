"""Testes de `crawler/flow/painel_flow.py::_semana_atual` — logica pura de
data, sem banco.

    python -m pytest tests/test_painel_flow.py -v
    python tests/test_painel_flow.py          (sem pytest instalado)
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from crawler.flow.painel_flow import _semana_atual  # noqa: E402


def test_semana_atual_numa_segunda():
    inicio, fim = _semana_atual(date(2026, 9, 7))  # segunda-feira
    assert inicio == date(2026, 9, 7)
    assert fim == date(2026, 9, 13)


def test_semana_atual_num_domingo():
    inicio, fim = _semana_atual(date(2026, 9, 13))  # domingo
    assert inicio == date(2026, 9, 7)
    assert fim == date(2026, 9, 13)


def test_semana_atual_meio_de_semana():
    inicio, fim = _semana_atual(date(2026, 9, 10))  # quinta-feira
    assert inicio == date(2026, 9, 7)
    assert fim == date(2026, 9, 13)


def test_semana_atual_padrao_e_hoje():
    inicio, fim = _semana_atual()
    hoje = date.today()
    assert inicio <= hoje <= fim


if __name__ == "__main__":
    falhas = 0
    testes = [(n, o) for n, o in sorted(globals().items())
              if n.startswith("test_") and callable(o)]
    for nome, func in testes:
        try:
            func()
            print(f"  ok   {nome}")
        except AssertionError as exc:
            falhas += 1
            print(f"  FALHA {nome}: {exc or 'assert'}")
        except Exception as exc:  # noqa: BLE001
            falhas += 1
            print(f"  ERRO  {nome}: {type(exc).__name__}: {exc}")
    print(f"\n{len(testes) - falhas}/{len(testes)} passaram")
    sys.exit(1 if falhas else 0)
