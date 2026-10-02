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
