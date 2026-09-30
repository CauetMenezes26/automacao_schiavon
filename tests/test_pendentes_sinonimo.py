"""Testes de conciliacao/pendentes_sinonimo.py.

Fixtures em memoria, sem rede — `sincronizar_pendentes` e testado com
`commons.sheets.read_values`/`append_values` trocados por fakes.

    python -m pytest tests/test_pendentes_sinonimo.py -v
    python tests/test_pendentes_sinonimo.py          (sem pytest instalado)
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import conciliacao.pendentes_sinonimo as mod  # noqa: E402
from conciliacao.pendentes_sinonimo import (  # noqa: E402
    PendenteRow,
    extrair_pendentes,
    sincronizar_pendentes,
)

_DATA = date(2026, 6, 25)


# ---------------------------------------------------------------------------
# extrair_pendentes
# ---------------------------------------------------------------------------

def test_item_sem_par_na_cotacao_entra():
    items = [{"description_invoice": "BEEF SIRLOIN", "issue_codes": ["no_quote_for_item"]}]
    pend = extrair_pendentes(items, "Cheney Brothers", "cotacao", _DATA)
    assert pend == [PendenteRow("Cheney Brothers", "BEEF SIRLOIN", "cotacao", "2026-06-25")]


def test_item_sem_par_no_po_entra():
    items = [{"description_invoice": "Pocos de Caldas Requeijao", "issue_codes": ["no_po_for_item"]}]
    pend = extrair_pendentes(items, "Triunfo Foods", "erp", _DATA)
    assert pend == [PendenteRow("Triunfo Foods", "Pocos de Caldas Requeijao", "erp", "2026-06-25")]


def test_item_com_outro_issue_code_nao_entra():
    items = [{"description_invoice": "BEEF SIRLOIN", "issue_codes": ["price_above_quote"]}]
    assert extrair_pendentes(items, "Cheney Brothers", "cotacao", _DATA) == []


def test_codigo_errado_pra_origem_nao_entra():
    # NO_QUOTE_FOR_ITEM so conta pra origem='cotacao', nao 'erp'.
    items = [{"description_invoice": "BEEF SIRLOIN", "issue_codes": ["no_quote_for_item"]}]
    assert extrair_pendentes(items, "Cheney Brothers", "erp", _DATA) == []


def test_item_sem_descricao_nao_entra():
    items = [{"description_invoice": None, "issue_codes": ["no_quote_for_item"]}]
    assert extrair_pendentes(items, "Cheney Brothers", "cotacao", _DATA) == []


def test_lista_vazia_nao_quebra():
    assert extrair_pendentes([], "Cheney Brothers", "cotacao", _DATA) == []


# ---------------------------------------------------------------------------
# sincronizar_pendentes
# ---------------------------------------------------------------------------

class _FakeSheet:
    """Substitui commons.sheets.read_values/append_values em memoria."""

    def __init__(self, existentes=None):
        self.existentes = existentes or [["FORNECEDOR", "ITEM", "ORIGEM", "DATA"]]
        self.gravados: list[list[str]] = []

    def read_values(self, _sheet_id, _range):
        return self.existentes

    def append_values(self, _sheet_id, _range, rows):
        self.gravados.extend(rows)


def _instalar_fake(monkeypatch, fake: _FakeSheet):
    monkeypatch.setattr(mod, "read_values", fake.read_values)
    monkeypatch.setattr(mod, "append_values", fake.append_values)


def test_sheet_id_ausente_nao_faz_nada(monkeypatch):
    fake = _FakeSheet()
    _instalar_fake(monkeypatch, fake)
    n = sincronizar_pendentes(None, [PendenteRow("X", "Y", "erp", "2026-06-25")])
    assert n == 0
    assert fake.gravados == []


def test_lista_vazia_nao_faz_nada(monkeypatch):
    fake = _FakeSheet()
    _instalar_fake(monkeypatch, fake)
    assert sincronizar_pendentes("sheet-1", []) == 0
    assert fake.gravados == []


def test_pendente_nova_e_gravada(monkeypatch):
    fake = _FakeSheet()
    _instalar_fake(monkeypatch, fake)
    novas = [PendenteRow("Cheney Brothers", "BEEF SIRLOIN", "cotacao", "2026-06-25")]
    n = sincronizar_pendentes("sheet-1", novas)
    assert n == 1
    assert fake.gravados == [["Cheney Brothers", "BEEF SIRLOIN", "cotacao", "2026-06-25"]]


def test_pendente_ja_existente_nao_duplica(monkeypatch):
    # Grafia diferente da que ja esta na aba, mas normaliza pro mesmo par.
    fake = _FakeSheet(existentes=[
        ["FORNECEDOR", "ITEM", "ORIGEM", "DATA"],
        ["cheney brothers", "beef  sirloin", "cotacao", "2026-06-01"],
    ])
    _instalar_fake(monkeypatch, fake)
    novas = [PendenteRow("Cheney Brothers", "Beef Sirloin", "cotacao", "2026-06-25")]
    n = sincronizar_pendentes("sheet-1", novas)
    assert n == 0
    assert fake.gravados == []


def test_dedupe_dentro_do_mesmo_lote(monkeypatch):
    fake = _FakeSheet()
    _instalar_fake(monkeypatch, fake)
    novas = [
        PendenteRow("Cheney Brothers", "BEEF SIRLOIN", "cotacao", "2026-06-25"),
        PendenteRow("cheney brothers", "beef sirloin", "cotacao", "2026-06-26"),
    ]
    n = sincronizar_pendentes("sheet-1", novas)
    assert n == 1
    assert len(fake.gravados) == 1


if __name__ == "__main__":
    class _Monkeypatch:
        """Substituto minimo de pytest.monkeypatch pro modo standalone."""

        def __init__(self):
            self._orig: list[tuple] = []

        def setattr(self, obj, name, value):
            self._orig.append((obj, name, getattr(obj, name)))
            setattr(obj, name, value)

        def undo(self):
            for obj, name, value in reversed(self._orig):
                setattr(obj, name, value)

    falhas = 0
    testes = [(n, o) for n, o in sorted(globals().items())
              if n.startswith("test_") and callable(o)]
    for nome, func in testes:
        mp = _Monkeypatch()
        try:
            if "monkeypatch" in func.__code__.co_varnames[:func.__code__.co_argcount]:
                func(mp)
            else:
                func()
            print(f"  ok   {nome}")
        except AssertionError as exc:
            falhas += 1
            print(f"  FALHA {nome}: {exc or 'assert'}")
        except Exception as exc:  # noqa: BLE001
            falhas += 1
            print(f"  ERRO  {nome}: {type(exc).__name__}: {exc}")
        finally:
            mp.undo()
    print(f"\n{len(testes) - falhas}/{len(testes)} passaram")
    sys.exit(1 if falhas else 0)
