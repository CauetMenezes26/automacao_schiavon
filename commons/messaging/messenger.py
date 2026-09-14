"""Envio de notificações de cotação via WhatsApp (Twilio Content API)."""

import os
import json
from datetime import datetime
from .twilio_config import Twilio


def _validate_twilio_env_vars() -> tuple[str, str]:
    """Valida e retorna as variáveis de ambiente obrigatórias do Twilio."""
    sender_number = os.getenv("TWILIO_NUMBER")
    content_sid = os.getenv("TWILIO_CONTENT_SID")

    missing = []
    if not sender_number:
        missing.append("TWILIO_NUMBER")
    if not content_sid:
        missing.append("TWILIO_CONTENT_SID")

    if missing:
        raise EnvironmentError(
            f"Variáveis de ambiente obrigatórias não configuradas no .env: {', '.join(missing)}"
        )

    return sender_number, content_sid


def send_whatsapp_quote_notification(
    recipient_whatsapp_number: str,
    supplier_sharepoint_url: str,
    supplier_name: str = "Fornecedor",
) -> dict:
    """
    Envia uma mensagem de WhatsApp interativa usando o template aprovado do Twilio.

    Template: "Cotação Semanal - Data: {{1}} - DataGuvi"
    Botão 1 (URL): "Preencher Cotação" → abre o link do Excel no SharePoint
    Botão 2 (Quick Reply): "Ausente esta Semana"

    Retorna um dicionário com os dados do envio para registro/log:
      - sid: identificador único da mensagem na Twilio
      - status: status inicial do envio (ex: 'queued', 'sent')
      - to: número de destino
      - date_sent: data/hora do envio
    """
    twilio_client = Twilio.returnClient()
    sender_whatsapp_number, twilio_template_content_sid = _validate_twilio_env_vars()

    formatted_current_date = datetime.now().strftime("%d/%m/%Y")

    if supplier_sharepoint_url.startswith("http"):
        sharepoint_full_url = supplier_sharepoint_url
    else:
        # :x: = link de arquivo Excel. Era :f: (pasta) — o fornecedor caía na
        # pasta inteira e via os Excels dos outros fornecedores.
        sharepoint_full_url = (
            f"https://dataguvicombr.sharepoint.com/:x:/s/DATA-GUVI-SCHIAVON/{supplier_sharepoint_url}"
        )

    twilio_template_variables = {
        "1": supplier_name,                # {{1}} no Body (Saudação)
        "2": formatted_current_date,       # {{2}} no Body (Data da cotação)
        "3": "Rokka",                      # {{3}} no Body (Parceiro)
        "4": sharepoint_full_url,          # {{4}} no Body (Link da planilha)
    }

    clean_sender = sender_whatsapp_number.replace("whatsapp:", "").replace("+", "").strip()
    clean_recipient = recipient_whatsapp_number.replace("whatsapp:", "").replace("+", "").strip()

    message = twilio_client.messages.create(
        from_=f"whatsapp:+{clean_sender}",
        to=f"whatsapp:+{clean_recipient}",
        content_sid=twilio_template_content_sid,
        content_variables=json.dumps(twilio_template_variables),
    )

    send_result = {
        "sid": message.sid,
        "status": message.status,
        "to": recipient_whatsapp_number,
        "date_sent": formatted_current_date,
    }

    print(
        f"✓ Cotação enviada para {recipient_whatsapp_number} "
        f"(SID: {message.sid} | Status: {message.status})"
    )
    return send_result
