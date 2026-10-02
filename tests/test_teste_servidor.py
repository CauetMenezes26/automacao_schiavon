"""Smoke test do servidor: orquestracao e classificacao OK/FALHA/PULADA."""

from __future__ import annotations

from domain.config import Config
from manutencao import teste_servidor as ts

BANCO = {"HOST": "h", "PORT": "5432", "DATABASE": "d", "USER_GUVI": "u", "PASSWORD_GUVI": "p"}


def _cfg(**extra) -> Config:
    return Config.de_valores({**BANCO, **extra})


def test_executar_classifica_ok_falha_e_pulada():
    def falha():
        raise RuntimeError("boom")

    def pula():
        raise ts._Pulada("chave ausente")

    assert ts._executar("a", lambda: "bom").status == ts.OK
    r = ts._executar("b", falha)
    assert r.status == ts.FALHA and "boom" in r.detalhe
    assert ts._executar("c", pula).status == ts.PULADA


def test_uma_falha_nao_impede_as_demais(monkeypatch):
    def cai(config):
        raise RuntimeError("banco fora")

    monkeypatch.setattr(ts, "_postgres", cai)
    monkeypatch.setattr(ts, "_chromium", lambda: "ok")
    monkeypatch.setattr(ts, "_gmail", lambda: "ok")
    resultados = ts.rodar(_cfg())
    por_nome = {r.nome: r.status for r in resultados}
    assert por_nome["PostgreSQL"] == ts.FALHA
    assert por_nome["Chromium (Playwright)"] == ts.OK
    assert len(resultados) == 8


def test_chaves_ausentes_viram_pulada_e_nao_falha():
    cfg = _cfg()
    assert ts._executar("claude", lambda: ts._claude(cfg)).status == ts.PULADA
    assert ts._executar("sheets", lambda: ts._sheets(cfg)).status == ts.PULADA
    assert ts._executar("smtp", lambda: ts._smtp(cfg)).status == ts.PULADA
    assert ts._executar("ecrs", lambda: ts._ecrs(cfg)).status == ts.PULADA


def test_opcionais_so_entram_com_flag(monkeypatch):
    for nome in ("_postgres", "_claude", "_sheets", "_smtp", "_ecrs",
                 "_email", "_login_sharepoint", "_login_catapult"):
        monkeypatch.setattr(ts, nome, lambda c: "ok")
    for nome in ("_chromium", "_gmail", "_pastas"):
        monkeypatch.setattr(ts, nome, lambda: "ok")
    assert len(ts.rodar(_cfg())) == 8
    assert len(ts.rodar(_cfg(), email=True, login=True)) == 11
