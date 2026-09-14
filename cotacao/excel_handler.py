"""Polling de respostas Twilio e tratamento de ausências por ciclo."""

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from commons.messaging.twilio_config import Twilio

PROCESSED_MESSAGES_REGISTRY = Path(__file__).resolve().parent.parent / "files" / "processed_twilio_messages.json"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _extract_only_digits(phone_number_raw: str) -> str:
    """Remove caracteres especiais e espaços, retornando apenas os dígitos numéricos."""
    return "".join(filter(str.isdigit, phone_number_raw))


def _extract_local_digits(phone_number_raw: str, local_length: int = 9) -> str:
    """Extrai os últimos N dígitos (número local) de um telefone."""
    digits = _extract_only_digits(phone_number_raw)
    return digits[-local_length:] if len(digits) >= local_length else digits


def _load_processed_message_sids() -> set[str]:
    """Carrega o registro local de SIDs de mensagens já processadas."""
    if PROCESSED_MESSAGES_REGISTRY.exists():
        try:
            data = json.loads(PROCESSED_MESSAGES_REGISTRY.read_text(encoding="utf-8"))
            return set(data)
        except (json.JSONDecodeError, TypeError):
            return set()
    return set()


def _save_processed_message_sid(message_sid: str) -> None:
    """Adiciona um SID de mensagem ao registro local de mensagens já processadas."""
    processed_sids = _load_processed_message_sids()
    processed_sids.add(message_sid)
    PROCESSED_MESSAGES_REGISTRY.parent.mkdir(parents=True, exist_ok=True)
    PROCESSED_MESSAGES_REGISTRY.write_text(
        json.dumps(sorted(processed_sids), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


# ---------------------------------------------------------------------------
# Polling de respostas da Twilio — ausência por ciclo
# ---------------------------------------------------------------------------

def check_twilio_absences(conn) -> int:
    """
    Busca respostas de 'Ausente esta Semana' recebidas na Twilio nas últimas 24h.
    Marca o quotation_response do ciclo aberto como 'ausente' (por ciclo, não permanente).

    Retorna a quantidade de fornecedores marcados como ausentes nesta execução.
    """
    from domain.service.cotacao_service import (
        fetch_open_quotation_request,
        fetch_sent_responses_by_supplier_phone,
        update_quotation_response_ausente,
    )

    twilio_client = Twilio.returnClient()
    twilio_number_raw = os.getenv("TWILIO_NUMBER")

    if not twilio_number_raw:
        raise EnvironmentError("Variável de ambiente TWILIO_NUMBER não configurada no .env.")

    twilio_number = twilio_number_raw.replace("whatsapp:", "").replace("+", "").strip()

    open_request = fetch_open_quotation_request(conn)
    if not open_request:
        print("Nenhum ciclo de cotação aberto. Nada a processar.")
        return 0

    phone_map = fetch_sent_responses_by_supplier_phone(conn, open_request["id"])

    print(f"  Fornecedores no ciclo (últimos 9 dígitos): {list(phone_map.keys())}")

    time_cutoff_24h = datetime.now(timezone.utc) - timedelta(hours=24)

    twilio_to = f"whatsapp:+{twilio_number}"
    print(f"  Consultando mensagens recebidas na Twilio (to={twilio_to})...")
    twilio_message_list = twilio_client.messages.list(
        to=twilio_to,
        date_sent_after=time_cutoff_24h,
    )

    received_client_messages = [msg for msg in twilio_message_list if msg.direction == "inbound"]
    print(f"  Total de mensagens inbound no período: {len(received_client_messages)}")

    already_processed_sids = _load_processed_message_sids()
    ausente_count = 0

    for message in received_client_messages:
        if message.sid in already_processed_sids:
            continue

        received_text = (message.body or "").strip()
        sender_phone = message.from_.replace("whatsapp:", "").replace("+", "")
        sender_local = _extract_local_digits(sender_phone)

        print(f"  MSG de {sender_local}: \"{received_text[:60]}\" (SID: {message.sid})")

        if "ausente" in received_text.lower():
            response_data = phone_map.get(sender_local)
            if response_data:
                update_quotation_response_ausente(conn, response_data["id"])
                print(f"  OK: {response_data['supplier_name']} marcado como ausente "
                      f"no ciclo {open_request['week_label']}")
                ausente_count += 1
                _save_processed_message_sid(message.sid)
            else:
                print(f"  ? Número {sender_phone} (local={sender_local}) "
                      f"não encontrado no mapa de fornecedores")

    if ausente_count == 0:
        print("  Nenhuma nova ausência identificada nesta execução.")

    return ausente_count
