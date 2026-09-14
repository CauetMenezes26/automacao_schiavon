"""Sistemas do fluxo — a fonte da verdade, em código.

Mesmo arranjo de `categorias.py` e `status_exec.py`: o banco (`dim_sistema`)
guarda o `codigo` e o status de acesso; o significado e o "de que env-vars ele
depende" moram aqui.

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

__all__ = ["Sistema", "CRITICOS", "env_keys", "checar_env"]


class Sistema(StrEnum):
    """Um sistema externo do qual o pipeline depende. Gravado em
    `dim_sistema.codigo`."""

    SHAREPOINT_WINDERMERE = "sharepoint_windermere"
    SHAREPOINT_DRPHILLIPS = "sharepoint_drphillips"
    TWILIO_WHATSAPP = "twilio_whatsapp"
    SMTP_EMAIL = "smtp_email"
    ANTHROPIC_VISION = "anthropic_vision"
    ERP_CATAPULT = "erp_catapult"

    @property
    def descricao(self) -> str:
        return _DESCRICAO[self]


_DESCRICAO: dict[Sistema, str] = {
    Sistema.SHAREPOINT_WINDERMERE: "SharePoint da loja Windermere (invoices e cotação).",
    Sistema.SHAREPOINT_DRPHILLIPS: "SharePoint da loja Dr. Phillips (invoices e cotação).",
    Sistema.TWILIO_WHATSAPP: "Twilio — envio de cotação e cobrança por WhatsApp.",
    Sistema.SMTP_EMAIL: "SMTP — envio de cotação por e-mail e alertas à operação.",
    Sistema.ANTHROPIC_VISION: "API Anthropic — leitura das invoices (Claude Vision).",
    Sistema.ERP_CATAPULT: "ERP Catapult — conciliação contra a nota (próximo ciclo).",
}

# env-vars que cada sistema precisa para sequer tentar autenticar. `checar_env`
# usa isto como proxy barato: chave ausente = acesso vai falhar.
_ENV_KEYS: dict[Sistema, tuple[str, ...]] = {
    Sistema.SHAREPOINT_WINDERMERE: ("SHAREPOINT_USERNAME", "SHAREPOINT_PASSWORD"),
    Sistema.SHAREPOINT_DRPHILLIPS: ("SHAREPOINT_USERNAME", "SHAREPOINT_PASSWORD"),
    Sistema.TWILIO_WHATSAPP: ("ACCOUNT_SID", "AUTH_TOKEN", "TWILIO_NUMBER", "TWILIO_CONTENT_SID"),
    Sistema.SMTP_EMAIL: ("SMTP_HOST", "SMTP_PORT", "SMTP_USER", "SMTP_PASSWORD", "SMTP_FROM"),
    Sistema.ANTHROPIC_VISION: ("schiavon_key_vision",),
    Sistema.ERP_CATAPULT: ("ECRS_USER", "ECRS_PASSWORD"),
}

# Sistemas cuja falha vira alerta para a operação. O ERP fica de fora até o
# fluxo existir.
CRITICOS: frozenset[str] = frozenset(s for s in Sistema if s is not Sistema.ERP_CATAPULT)


def env_keys(sistema: Sistema) -> tuple[str, ...]:
    return _ENV_KEYS[sistema]


def checar_env(sistema: Sistema, env: dict) -> tuple[bool, str | None]:
    """Proxy barato de "dá para autenticar?": todas as env-vars presentes.

    Não faz chamada de rede — o `acesso_ok` real vem das falhas de login dentro
    dos fluxos. Retorna (ok, mensagem_de_erro_ou_None).
    """
    faltando = [k for k in _ENV_KEYS[sistema] if not (env.get(k) or "").strip()]
    if faltando:
        return False, f"env ausente: {', '.join(faltando)}"
    return True, None
