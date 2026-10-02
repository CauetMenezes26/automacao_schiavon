"""Transporte de e-mail (SMTP) — um lugar só.

`cotacao/email_sender.py` (cotação ao fornecedor) e
`domain/service/sistema_service.py` (alerta à operação) usam `enviar_email`.
A config chega como `ConfigSmtp`, montada em `domain/config.py` a partir do
profile — este módulo é `commons` e não lê profile nem ambiente.
"""

from __future__ import annotations

import smtplib
import ssl
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

from .exception import ConfigException
from commons.logging_config import get_logger

log = get_logger(__name__)

__all__ = ["enviar_email", "ConfigSmtp"]

# Sem timeout o smtplib espera para sempre e trava o pipeline inteiro.
_TIMEOUT_S = 30
_PORTA_SSL = 465


@dataclass(frozen=True)
class ConfigSmtp:
    host: str
    port: str
    usuario: str
    senha: str = field(repr=False)
    remetente: str

    def faltando(self) -> list[str]:
        """Chaves do profile ainda nao preenchidas, para a mensagem de erro."""
        return [
            chave for chave, valor in (
                ("SMTP_HOST", self.host), ("SMTP_PORT", self.port),
                ("SMTP_USER", self.usuario), ("SMTP_PASSWORD", self.senha),
                ("SMTP_FROM", self.remetente),
            ) if not valor
        ]


def enviar_email(
    smtp: ConfigSmtp,
    destino: str,
    assunto: str,
    corpo_html: str,
    corpo_txt: str | None = None,
    anexos: Sequence[Path] = (),
) -> dict:
    """Envia um e-mail. Retorna dict de log; não levanta em falha de SMTP
    (retorna `status='error'`), mas levanta `ConfigException` se o profile não
    tem as chaves — quem chama decide o que fazer com config ausente.

    `anexos`: arquivos a anexar; caminho inexistente e ignorado com WARNING."""
    faltando = smtp.faltando()
    if faltando:
        raise ConfigException(
            f"SMTP não configurado: falta {', '.join(faltando)} no profile."
        )
    agora = datetime.now().strftime("%d/%m/%Y %H:%M")

    msg = MIMEMultipart("mixed")
    msg["Subject"] = assunto
    msg["From"] = smtp.remetente
    msg["To"] = destino
    corpo = MIMEMultipart("alternative")
    corpo.attach(MIMEText(corpo_txt or _html_para_txt(corpo_html), "plain", "utf-8"))
    corpo.attach(MIMEText(corpo_html, "html", "utf-8"))
    msg.attach(corpo)
    for caminho in anexos:
        _anexar(msg, Path(caminho))

    try:
        with _abrir_conexao(smtp) as server:
            server.login(smtp.usuario, smtp.senha)
            server.send_message(msg)
        log.info("e-mail enviado para %s", destino)
        return {"status": "sent", "to": destino, "date_sent": agora}
    except Exception as exc:  # noqa: BLE001 — o chamador loga/alerta
        log.error("falha ao enviar e-mail para %s: %s", destino, exc)
        return {"status": "error", "to": destino, "date_sent": agora, "error": str(exc)}


def _abrir_conexao(smtp: ConfigSmtp) -> smtplib.SMTP:
    """Porta 465 = SSL direto (`SMTP_SSL`); qualquer outra (587) = STARTTLS.

    A 587 pode ser bloqueada por firewall/antivirus (a conexao abre e o
    servidor nunca responde); a 465 costuma passar. A escolha e so o
    `SMTP_PORT` do profile.
    """
    porta = int(smtp.port)
    if porta == _PORTA_SSL:
        return smtplib.SMTP_SSL(
            smtp.host, porta, timeout=_TIMEOUT_S, context=ssl.create_default_context(),
        )
    server = smtplib.SMTP(smtp.host, porta, timeout=_TIMEOUT_S)
    server.starttls()
    return server


def _anexar(msg: MIMEMultipart, caminho: Path) -> None:
    """Anexa o arquivo ao e-mail. Arquivo ausente/ilegivel e so aviso."""
    if not caminho.is_file():
        log.warning("anexo inexistente, ignorado: %s", caminho)
        return
    parte = MIMEApplication(caminho.read_bytes(), Name=caminho.name)
    parte["Content-Disposition"] = f'attachment; filename="{caminho.name}"'
    msg.attach(parte)


def _html_para_txt(html: str) -> str:
    """Fallback grosseiro de texto puro quando o chamador não passa um."""
    import re

    txt = re.sub(r"<(script|style)[^>]*>.*?</\1>", "", html, flags=re.S | re.I)
    txt = re.sub(r"<[^>]+>", " ", txt)
    return re.sub(r"\s+", " ", txt).strip()
