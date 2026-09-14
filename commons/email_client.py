"""Transporte de e-mail (SMTP) — um lugar só.

O núcleo SMTP vivia em `cotacao/email_sender.py`, que lia `SMTP_*` de
`os.getenv`. O resto do projeto passa `env` (dict de `load_env`) adiante, então
aqui a config vem do `env`. `cotacao/email_sender.py` (cotação ao fornecedor) e
`utils/monitor.py` (alerta à operação) usam esta função.
"""

from __future__ import annotations

import smtplib
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

__all__ = ["enviar_email", "SMTP_KEYS"]

SMTP_KEYS = ("SMTP_HOST", "SMTP_PORT", "SMTP_USER", "SMTP_PASSWORD", "SMTP_FROM")


def _config(env: dict) -> dict[str, str]:
    cfg = {k: (env.get(k) or "").strip() for k in SMTP_KEYS}
    faltando = [k for k in SMTP_KEYS if not cfg[k]]
    if faltando:
        raise EnvironmentError(
            f"SMTP não configurado: falta {', '.join(faltando)} no .env."
        )
    return cfg


def enviar_email(
    env: dict,
    destino: str,
    assunto: str,
    corpo_html: str,
    corpo_txt: str | None = None,
) -> dict:
    """Envia um e-mail. Retorna dict de log; não levanta em falha de SMTP
    (retorna `status='error'`), mas levanta `EnvironmentError` se o `.env` não
    tem as chaves — quem chama decide o que fazer com config ausente."""
    cfg = _config(env)
    agora = datetime.now().strftime("%d/%m/%Y %H:%M")

    msg = MIMEMultipart("alternative")
    msg["Subject"] = assunto
    msg["From"] = cfg["SMTP_FROM"]
    msg["To"] = destino
    msg.attach(MIMEText(corpo_txt or _html_para_txt(corpo_html), "plain", "utf-8"))
    msg.attach(MIMEText(corpo_html, "html", "utf-8"))

    try:
        with smtplib.SMTP(cfg["SMTP_HOST"], int(cfg["SMTP_PORT"])) as server:
            server.starttls()
            server.login(cfg["SMTP_USER"], cfg["SMTP_PASSWORD"])
            server.send_message(msg)
        print(f"  ✓ e-mail enviado para {destino}")
        return {"status": "sent", "to": destino, "date_sent": agora}
    except Exception as exc:  # noqa: BLE001 — o chamador loga/alerta
        print(f"  ✗ falha ao enviar e-mail para {destino}: {exc}")
        return {"status": "error", "to": destino, "date_sent": agora, "error": str(exc)}


def _html_para_txt(html: str) -> str:
    """Fallback grosseiro de texto puro quando o chamador não passa um."""
    import re

    txt = re.sub(r"<(script|style)[^>]*>.*?</\1>", "", html, flags=re.S | re.I)
    txt = re.sub(r"<[^>]+>", " ", txt)
    return re.sub(r"\s+", " ", txt).strip()
