"""Testes do de-para de sinonimos de item (conciliacao/sinonimos.py).

Fixtures em memoria — nao tocam o Postgres. As planilhas sao geradas em disco
temporario com openpyxl.

    python -m pytest tests/test_sinonimos.py -v
    python tests/test_sinonimos.py          (sem pytest instalado)
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import openpyxl  # noqa: E402

from domain.service.conciliacao_service import upsert_item_sinonimos  # noqa: E402
from conciliacao.sinonimos import (  # noqa: E402
    Sinonimo,
    _sincronizar,
    sinonimos_de_planilha,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mk_xlsx(rows, header=("DE", "PARA"), directory=None, name="DE_PARA_ITENS.xlsx"):
    """Grava uma planilha e devolve o Path."""
    directory = Path(directory) if directory else Path(tempfile.mkdtemp())
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    wb = openpyxl.Workbook()
    ws = wb.active
    if header is not None:
        ws.append(list(header))
    for r in rows:
        ws.append(list(r))
    wb.save(path)
    return path


class _FakeCursor:
    def __init__(self, conn):
        self.conn = conn
        self.rowcount = 0

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def executemany(self, sql, seq):
        self.conn.execs.append((sql, list(seq)))

    def execute(self, sql, *a):
        self._last = sql

    def fetchall(self):
        # fetch_item_sinonimos espera pares (termo_norm, canonico)
        return list(self.conn.sinonimos_no_banco.items())

    def fetchone(self):
        # _contar (marcar_reprocesso_por_vocabulario) espera (n,)
        return (self.conn.notas_para_reprocesso,)


class _FakeConn:
    """Registra os executemany; o suficiente para upsert_item_sinonimos e para
    a deteccao de mudanca de vocabulario (fetch_item_sinonimos / _contar)."""

    def __init__(self, sinonimos_no_banco=None, notas_para_reprocesso=0):
        self.execs: list = []
        self.sinonimos_no_banco = dict(sinonimos_no_banco or {})
        self.notas_para_reprocesso = notas_para_reprocesso

    def cursor(self):
        return _FakeCursor(self)

    def commit(self):
        pass


# ---------------------------------------------------------------------------
# Leitura / validacao da planilha
# ---------------------------------------------------------------------------

def test_le_de_para_valido():
    path = _mk_xlsx([("Chix", "Chicken"), ("Sassami", "Tender"), ("Boi", "Beef")])
    rows, rel = sinonimos_de_planilha(path)
    assert rel.validas == 3
    assert rel.invalidas == 0 and rel.duplicadas == 0
    assert Sinonimo("Chix", "CHIX", "CHICKEN") in rows


def test_excel_sem_coluna_obrigatoria_falha():
    path = _mk_xlsx([("Chix", "Chicken")], header=("DE", "OUTRA"))
    try:
        sinonimos_de_planilha(path)
    except ValueError as exc:
        assert "PARA" in str(exc)
    else:
        raise AssertionError("esperava ValueError por coluna faltando")


def test_colunas_em_qualquer_ordem():
    path = _mk_xlsx([("Chicken", "Chix")], header=("PARA", "DE"))
    rows, rel = sinonimos_de_planilha(path)
    assert rows == [Sinonimo("Chix", "CHIX", "CHICKEN")]
    assert rel.validas == 1


def test_linha_vazia_e_ignorada_sem_contar_como_invalida():
    path = _mk_xlsx([("Chix", "Chicken"), (None, None), ("Sassami", "Tender")])
    rows, rel = sinonimos_de_planilha(path)
    assert rel.lidas == 2 and rel.validas == 2 and rel.invalidas == 0


def test_linha_com_um_lado_so_e_invalida():
    path = _mk_xlsx([("Chix", None), (None, "Chicken")])
    rows, rel = sinonimos_de_planilha(path)
    assert rows == []
    assert rel.invalidas == 2
    assert all("um lado" in motivo for _, motivo in rel.descartes)


def test_termo_multi_token_e_invalido():
    # 'TRI TIP' tem espaco: nunca casaria no .split() de norm_item.
    path = _mk_xlsx([("Tri Tip", "Maminha"), ("Chix", "Chicken")])
    rows, rel = sinonimos_de_planilha(path)
    assert [s.termo_norm for s in rows] == ["CHIX"]
    assert rel.invalidas == 1
    assert "mais de um token" in rel.descartes[0][1]


def test_duplicidade_no_arquivo_ultima_vence():
    path = _mk_xlsx([("Chix", "Chicken"), ("chix", "Poultry")])
    rows, rel = sinonimos_de_planilha(path)
    assert rel.validas == 1 and rel.duplicadas == 1
    assert rows[0] == Sinonimo("chix", "CHIX", "POULTRY")


def test_normalizacao_acento_e_nbsp():
    path = _mk_xlsx([("Fígado", "Liver"), ("Coração\xa0", "Hearts")])
    rows, _ = sinonimos_de_planilha(path)
    assert {s.termo_norm for s in rows} == {"FIGADO", "CORACAO"}


def test_leitura_e_idempotente():
    path = _mk_xlsx([("Chix", "Chicken"), ("Sassami", "Tender")])
    a, _ = sinonimos_de_planilha(path)
    b, _ = sinonimos_de_planilha(path)
    assert a == b


# ---------------------------------------------------------------------------
# UPSERT
# ---------------------------------------------------------------------------

def test_upsert_sql_e_on_conflict():
    fake = _FakeConn()
    upsert_item_sinonimos(fake, [Sinonimo("Chix", "CHIX", "CHICKEN")])
    sql, seq = fake.execs[0]
    assert "ON CONFLICT (termo_norm)" in sql
    assert seq == [("Chix", "CHIX", "CHICKEN")]


def test_upsert_vazio_nao_toca_o_banco():
    fake = _FakeConn()
    assert upsert_item_sinonimos(fake, []) == 0
    assert fake.execs == []


# ---------------------------------------------------------------------------
# Sincronizacao (preambulo do pipeline)
# ---------------------------------------------------------------------------

def test_sincronizar_grava_via_upsert_e_e_idempotente():
    d = Path(tempfile.mkdtemp())
    _mk_xlsx([("Chix", "Chicken"), ("Sassami", "Tender")], directory=d)

    fake = _FakeConn()
    _sincronizar(fake, directory=d)
    _sincronizar(fake, directory=d)

    assert len(fake.execs) == 2
    assert fake.execs[0][1] == fake.execs[1][1]          # mesmo payload nas duas
    assert sorted(fake.execs[0][1]) == [
        ("Chix", "CHIX", "CHICKEN"), ("Sassami", "SASSAMI", "TENDER"),
    ]


def test_sincronizar_so_reprocessa_quando_vocabulario_muda():
    import domain.service.conciliacao_service as cdb

    chamadas: list = []
    original = cdb.marcar_reprocesso_por_vocabulario
    cdb.marcar_reprocesso_por_vocabulario = lambda conn: chamadas.append(1) or 0
    try:
        d = Path(tempfile.mkdtemp())
        _mk_xlsx([("Chix", "Chicken"), ("Sassami", "Tender")], directory=d)

        # Banco já com o mesmo de-para: nada mudou -> não remarca.
        ja_igual = {"CHIX": "CHICKEN", "SASSAMI": "TENDER"}
        _sincronizar(_FakeConn(sinonimos_no_banco=ja_igual), directory=d)
        assert chamadas == []

        # Banco com destino diferente para SASSAMI: mudou -> remarca uma vez.
        difere = {"CHIX": "CHICKEN", "SASSAMI": "STRIP"}
        _sincronizar(_FakeConn(sinonimos_no_banco=difere), directory=d)
        assert chamadas == [1]
    finally:
        cdb.marcar_reprocesso_por_vocabulario = original


def test_sincronizar_pula_planilha_travada():
    d = Path(tempfile.mkdtemp())
    _mk_xlsx([("Chix", "Chicken")], directory=d)

    def _trava(_path):
        raise PermissionError("arquivo aberto no Excel")

    fake = _FakeConn()
    rel = _sincronizar(fake, directory=d, _leitor=_trava)
    assert rel is None
    assert fake.execs == []          # nada gravado, pipeline segue


def test_sincronizar_sem_arquivo_retorna_none():
    d = Path(tempfile.mkdtemp())
    fake = _FakeConn()
    assert _sincronizar(fake, directory=d) is None
    assert fake.execs == []


def test_sincronizar_ignora_lock_do_excel():
    d = Path(tempfile.mkdtemp())
    (d / "~$DE_PARA_ITENS.xlsx").write_bytes(b"")     # so o lock, nenhum .xlsx real
    fake = _FakeConn()
    assert _sincronizar(fake, directory=d) is None


def test_sincronizar_diretorio_inexistente_retorna_none():
    fake = _FakeConn()
    assert _sincronizar(fake, directory=Path(tempfile.gettempdir()) / "nao_existe_xyz") is None


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
