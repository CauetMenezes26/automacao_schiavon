"""Fachada movida para `crawler.flow.invoices_flow`. Shim de compatibilidade (Fase 3)."""

from crawler.flow.invoices_flow import (  # noqa: F401
    MODO_SINCRONO,
    coletar_invoices,
    invoices_flow,
)
