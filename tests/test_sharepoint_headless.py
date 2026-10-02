"""Spec headless-sharepoint: padrao headless, viewport largo, downloads habilitados."""

import inspect

from commons import sharepoint


class _Browser:
    def __init__(self):
        self.kwargs = None

    def new_context(self, **kwargs):
        self.kwargs = kwargs
        return object()


def test_process_all_configs_e_headless_por_padrao():
    padrao = inspect.signature(sharepoint.process_all_configs).parameters["headless"].default
    assert padrao is True


def test_open_sharepoint_session_e_headless_por_padrao():
    padrao = inspect.signature(sharepoint.open_sharepoint_session).parameters["headless"].default
    assert padrao is True


def test_novo_contexto_usa_viewport_largo_e_downloads():
    browser = _Browser()
    sharepoint._novo_contexto(browser)
    assert browser.kwargs["viewport"] == {"width": 1920, "height": 945}
    assert browser.kwargs["accept_downloads"] is True


def test_login_que_falha_fecha_browser_e_para_playwright(monkeypatch):
    """Sem isso, o Playwright fica ativo e a proxima sessao do processo quebra
    com 'Sync API inside the asyncio loop'."""
    import sys
    import types

    from commons import sharepoint as sp

    eventos = []

    class Pagina:
        def goto(self, *a, **k): pass

    class Contexto:
        def new_page(self): return Pagina()

    class Browser:
        def new_context(self, **k): return Contexto()
        def close(self): eventos.append("browser.close")

    class PW:
        chromium = types.SimpleNamespace(launch=lambda **k: Browser())
        def stop(self): eventos.append("pw.stop")

    fake = types.ModuleType("playwright.sync_api")
    fake.sync_playwright = lambda: types.SimpleNamespace(start=lambda: PW())
    monkeypatch.setitem(sys.modules, "playwright.sync_api", fake)

    def recusa(*a, **k):
        raise sp.SharePointLoginError("recusado")

    monkeypatch.setattr(sp, "_handle_microsoft_login", recusa)
    import pytest
    with pytest.raises(sp.SharePointLoginError):
        sp.open_sharepoint_session("u", "s", "https://x.sharepoint.com/a")
    assert eventos == ["browser.close", "pw.stop"]


def test_preencher_usuario_redigita_se_o_fill_nao_registrou():
    from commons import sharepoint as sp

    class Campo:
        def __init__(self):
            self.valor = ""
            self.digitado = None
        def fill(self, v): self.valor = "" if v == "" else ""  # fill nao "pega"
        def input_value(self): return self.valor
        def press_sequentially(self, v, delay=0): self.digitado = v; self.valor = v

    campo = Campo()
    page = type("P", (), {"locator": lambda self, sel: campo})()
    sp._preencher_usuario(page, "a@b.com")
    assert campo.digitado == "a@b.com" and campo.valor == "a@b.com"
