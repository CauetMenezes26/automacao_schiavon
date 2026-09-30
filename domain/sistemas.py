"""Sistemas do fluxo — a fonte da verdade, em código.

Mesmo arranjo de `categorias.py` e `status_exec.py`: o banco (`dim_sistema`)
guarda o `codigo` e o status de acesso; o significado mora aqui. De que chaves do
profile cada sistema depende: `domain/config.py::Config.checar_sistema`.

POR QUE ESTE MÓDULO EXISTE

O painel pede "trazer todos os sistemas do fluxo" e "acesso ok". `dim_fonte` só
cobre SharePoint/ERP por loja e não tem status. Twilio, SMTP e a API da Anthropic
não estavam registrados em lugar nenhum — uma falha de login/config deles só
aparecia no terminal.

`dim_sistema` é o inventário + saúde (in-place: `ultimo_ok` / `ultimo_erro` /
`ultima_mensagem`). `dim_fonte` continua sendo "de onde uma loja coleta" (URL por
loja); os dois não se misturam.
"""

from __future__ import annotations

from enum import StrEnum

__all__ = ["Sistema", "CRITICOS"]


class Sistema(StrEnum):
    """Um sistema externo do qual o pipeline depende. Gravado em
    `dim_sistema.codigo`."""

    SHAREPOINT_WINDERMERE = "sharepoint_windermere"
    SHAREPOINT_DRPHILLIPS = "sharepoint_drphillips"
    TWILIO_WHATSAPP = "twilio_whatsapp"
    SMTP_EMAIL = "smtp_email"
    ANTHROPIC_VISION = "anthropic_vision"
    ERP_CATAPULT = "erp_catapult"
    GOOGLE_SHEETS = "google_sheets"

    @property
    def descricao(self) -> str:
        return _DESCRICAO[self]


_DESCRICAO: dict[Sistema, str] = {
    Sistema.SHAREPOINT_WINDERMERE: "SharePoint da loja Windermere (invoices e cotação).",
    Sistema.SHAREPOINT_DRPHILLIPS: "SharePoint da loja Dr. Phillips (invoices e cotação).",
    Sistema.TWILIO_WHATSAPP: "Twilio — envio de cotação e cobrança por WhatsApp.",
    Sistema.SMTP_EMAIL: "SMTP — envio de cotação por e-mail e alertas à operação.",
    Sistema.ANTHROPIC_VISION: "API Anthropic — leitura das invoices (Claude Vision).",
    Sistema.ERP_CATAPULT: "ERP Catapult — conciliação da nota contra o PO (FLUXO 4).",
    Sistema.GOOGLE_SHEETS: "Google Sheets — planilha De-Para/Pendentes de sinônimo de item.",
}

# Sistemas cuja falha vira alerta para a operação. O ERP entrou quando o
# FLUXO 4 (crawler/flow/reconcile_erp_flow.py) passou a existir de verdade —
# NOTA: o alerta de fato depende também de `dim_sistema.critico` no banco
# (dado, não código — ver domain/service/sistema_service.py::verificar);
# atualizar a linha do erp_catapult lá para isto valer na prática.
CRITICOS: frozenset[str] = frozenset(s for s in Sistema)
