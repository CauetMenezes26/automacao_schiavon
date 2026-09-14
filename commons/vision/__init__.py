"""Leitura de invoices (PDF e imagens) via Claude Vision API."""

from __future__ import annotations

import base64
import json
import re
import time
from pathlib import Path
from typing import Literal, NamedTuple

import anthropic

# Exceção tolerada à regra das setas: `InvoiceData`/`InvoiceItem` são schema puro
# (forma do dado que o Vision preenche), não regra de negócio. O cliente devolve
# esses objetos; a persistência fica no fluxo. Ver `.cursor/rules/estrutura-projeto.mdc`.
from domain.model.invoice import InvoiceData, InvoiceItem


# ---------------------------------------------------------------------------
# Prompt de extração
# ---------------------------------------------------------------------------

_PROMPT = """\
You are an expert at reading and extracting structured data from vendor invoices,
including digital PDFs and scanned paper documents.

Extract ALL information visible in this invoice and return ONLY a valid JSON object
(no markdown, no explanation — raw JSON only).

Pay special attention to:

1. HANDWRITTEN ANNOTATIONS — pen-written quantity/price corrections, return notes,
   approval stamps. Capture in `handwritten_notes` of the affected item.

2. DATES — ISO 8601 format: YYYY-MM-DD.

3. AMOUNTS — separate subtotal, tax, and total.

4. LINE ITEMS — every row, in order.

5. READING CONFIDENCE — integer 0-100:
   90-100: all clear | 70-89: minor issues | 50-69: significant issues | 0-49: major problems

Return this exact JSON structure (use null for unknown fields):
{
  "invoice_number": null,
  "invoice_date": null,
  "due_date": null,
  "currency": "USD",
  "subtotal": null,
  "tax_amount": null,
  "total_amount": null,
  "supplier_name": null,
  "supplier_address": null,
  "supplier_phone": null,
  "supplier_email": null,
  "supplier_tax_id": null,
  "client_name": null,
  "client_address": null,
  "client_tax_id": null,
  "items": [
    {
      "item_order": 1,
      "description": null,
      "quantity": null,
      "unit": null,
      "unit_price": null,
      "total_price": null,
      "handwritten_notes": null
    }
  ],
  "reading_confidence": 95,
  "reading_status": "success",
  "reading_notes": null
}
"""


# ---------------------------------------------------------------------------
# Funções de leitura
# ---------------------------------------------------------------------------

def _repair_truncated_json(s: str) -> dict:
    """
    Tenta reparar JSON truncado fechando colchetes/chaves abertas.
    Usado quando max_tokens é atingido no meio do JSON.
    """
    # Conta abertura/fechamento de estruturas
    stack = []
    in_string = False
    escape = False

    for ch in s:
        if escape:
            escape = False
            continue
        if ch == "\\" and in_string:
            escape = True
            continue
        if ch == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch in "{[":
            stack.append("}" if ch == "{" else "]")
        elif ch in "}]":
            if stack and stack[-1] == ch:
                stack.pop()

    # Fecha tudo o que ficou aberto
    repaired = s.rstrip().rstrip(",") + "".join(reversed(stack))

    try:
        return json.loads(repaired)
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"JSON truncado e não foi possível reparar: {exc}. "
            f"Tente aumentar max_tokens ou simplificar o prompt."
        ) from exc


def _media_type(suffix: str) -> tuple[str, str]:
    """Returns (block_type, media_type) for a given file extension."""
    mapping = {
        ".pdf":  ("document", "application/pdf"),
        ".jpg":  ("image",    "image/jpeg"),
        ".jpeg": ("image",    "image/jpeg"),
        ".png":  ("image",    "image/png"),
        ".webp": ("image",    "image/webp"),
        ".gif":  ("image",    "image/gif"),
    }
    result = mapping.get(suffix.lower())
    if result is None:
        raise ValueError(f"Formato não suportado: '{suffix}'. Use PDF, JPG, PNG, WEBP ou GIF.")
    return result


def read_invoice(file_path: Path, api_key: str, model: str = "claude-sonnet-4-6") -> InvoiceData:
    """
    Lê um arquivo de invoice (PDF ou imagem) e retorna os dados estruturados
    extraídos pelo Claude Vision (claude-opus-4-8).

    Args:
        file_path: Caminho para o arquivo (PDF, JPG, PNG, etc.)
        api_key:   Chave da API Anthropic — lida do .env como 'schiavon_key_vision'

    Returns:
        InvoiceData com todos os campos extraídos e indicador de confiança.
    """
    client = anthropic.Anthropic(api_key=api_key)

    block_type, media_type = _media_type(file_path.suffix)
    file_b64 = base64.standard_b64encode(file_path.read_bytes()).decode("utf-8")

    content_block: dict = {
        "type": block_type,
        "source": {
            "type": "base64",
            "media_type": media_type,
            "data": file_b64,
        },
    }

    response = client.messages.create(
        model=model,
        max_tokens=8192,
        thinking={"type": "disabled"},
        messages=[
            {
                "role": "user",
                "content": [
                    content_block,
                    {"type": "text", "text": _PROMPT},
                ],
            }
        ],
    )

    # Avisa se o modelo parou por limite de tokens (JSON provavelmente truncado)
    if response.stop_reason == "max_tokens":
        print(f"    ⚠ stop_reason=max_tokens — JSON pode estar truncado "
              f"({response.usage.output_tokens} tokens de saída)")

    raw = response.content[0].text

    # Extrai o JSON da resposta (remove eventual texto ao redor)
    match = re.search(r"\{.*\}", raw, re.DOTALL)
    if not match:
        raise ValueError(f"Resposta da API não contém JSON válido: {raw[:200]}")

    json_str = match.group()

    # Tenta parsear; se falhar por truncamento, tenta fechar o JSON
    try:
        data = json.loads(json_str)
    except json.JSONDecodeError:
        data = _repair_truncated_json(json_str)

    # Preços por milhão de tokens (USD) — preço cheio para chamadas síncronas
    usage = response.usage
    _PRICES_SYNC = {
        "claude-haiku-4-5":  (1.00,  5.00),
        "claude-sonnet-4-6": (3.00, 15.00),
        "claude-opus-4-8":   (5.00, 25.00),
    }
    price_in, price_out = _PRICES_SYNC.get(model, (5.00, 25.00))
    cost = (usage.input_tokens * price_in + usage.output_tokens * price_out) / 1_000_000

    data["model_ai"] = model
    data["cost_read"] = round(cost, 6)

    return InvoiceData(**data)


def read_invoices_from_dir(
    directory: Path,
    api_key: str,
    extensions: tuple[str, ...] = (".pdf", ".jpg", ".jpeg", ".png"),
    model: str = "claude-sonnet-4-6",
) -> list[tuple[Path, InvoiceData]]:
    """
    Lê todos os invoices de um diretório.
    Retorna lista de (caminho_do_arquivo, dados_extraídos).
    """
    results = []
    files = [f for f in directory.iterdir() if f.suffix.lower() in extensions]

    for file_path in sorted(files):
        print(f"  Lendo: {file_path.name}")
        try:
            data = read_invoice(file_path, api_key, model)
            print(f"    ✓ confiança={data.reading_confidence:.0f}%  "
                  f"status={data.reading_status}  itens={len(data.items)}")
            results.append((file_path, data))
        except Exception as exc:
            print(f"    ✗ Erro: {exc}")

    return results


# ---------------------------------------------------------------------------
# Batch API — 50% de desconto, processamento paralelo
# ---------------------------------------------------------------------------

_PRICES = {
    "claude-haiku-4-5":  (0.50,  2.50),   # 50% do preço normal
    "claude-sonnet-4-6": (1.50,  7.50),
    "claude-opus-4-8":   (2.50, 12.50),
}


class BatchItem(NamedTuple):
    file_path: Path
    config_id: int          # id da loja em dim_loja
    config_name: str


def _build_content_block(file_path: Path) -> dict:
    block_type, media_type = _media_type(file_path.suffix)
    file_b64 = base64.standard_b64encode(file_path.read_bytes()).decode("utf-8")
    return {
        "type": block_type,
        "source": {"type": "base64", "media_type": media_type, "data": file_b64},
    }


def _parse_invoice_json(raw: str, usage, model: str) -> InvoiceData:
    """Extrai e valida o JSON retornado pelo modelo."""
    match = re.search(r"\{.*\}", raw, re.DOTALL)
    if not match:
        raise ValueError(f"Resposta sem JSON válido: {raw[:200]}")
    json_str = match.group()
    try:
        data = json.loads(json_str)
    except json.JSONDecodeError:
        data = _repair_truncated_json(json_str)

    price_in, price_out = _PRICES.get(model, (2.50, 12.50))
    cost = (usage.input_tokens * price_in + usage.output_tokens * price_out) / 1_000_000
    data["model_ai"] = model
    data["cost_read"] = round(cost, 6)
    return InvoiceData(**data)


def submit_batch(
    items: list[BatchItem],
    api_key: str,
    model: str = "claude-sonnet-4-6",
) -> tuple[str, dict[str, BatchItem]]:
    """
    Envia todos os arquivos para o Batch API em uma única requisição.

    Retorna:
        batch_id    — ID do batch para consulta posterior
        id_map      — mapeamento custom_id → BatchItem
    """
    from anthropic.types.message_create_params import MessageCreateParamsNonStreaming
    from anthropic.types.messages.batch_create_params import Request

    client = anthropic.Anthropic(api_key=api_key)
    id_map: dict[str, BatchItem] = {}
    requests: list[Request] = []

    for idx, item in enumerate(items):
        custom_id = f"inv_{idx:04d}"
        id_map[custom_id] = item

        requests.append(Request(
            custom_id=custom_id,
            params=MessageCreateParamsNonStreaming(
                model=model,
                max_tokens=8192,
                thinking={"type": "disabled"},
                messages=[{
                    "role": "user",
                    "content": [
                        _build_content_block(item.file_path),
                        {"type": "text", "text": _PROMPT},
                    ],
                }],
            ),
        ))

    print(f"  Enviando {len(requests)} arquivo(s) para o Batch API...")
    batch = client.messages.batches.create(requests=requests)
    print(f"  Batch criado: {batch.id}")
    return batch.id, id_map


def wait_for_batch(
    batch_id: str,
    api_key: str,
    poll_interval: int = 30,
    max_wait_seconds: int = 86_400,
) -> None:
    """Aguarda o batch terminar, exibindo progresso a cada intervalo.

    Lança TimeoutError se max_wait_seconds for atingido (padrão: 24h).
    """
    client = anthropic.Anthropic(api_key=api_key)
    print(f"  Aguardando conclusão do batch {batch_id}...")
    start = time.time()

    while True:
        elapsed = int(time.time() - start)
        if elapsed > max_wait_seconds:
            raise TimeoutError(
                f"Batch {batch_id} não concluiu em {max_wait_seconds}s — "
                "verifique o status no painel da Anthropic."
            )

        batch = client.messages.batches.retrieve(batch_id)
        counts = batch.request_counts
        print(f"    [{elapsed}s] status={batch.processing_status}  "
              f"processando={counts.processing}  "
              f"concluídos={counts.succeeded}  "
              f"erros={counts.errored}")

        if batch.processing_status == "ended":
            break
        time.sleep(poll_interval)


def collect_batch_results(
    batch_id: str,
    api_key: str,
    id_map: dict[str, BatchItem],
    model: str = "claude-sonnet-4-6",
) -> list[tuple[BatchItem, InvoiceData | None, str | None]]:
    """
    Coleta os resultados do batch.

    Retorna lista de (BatchItem, InvoiceData | None, erro | None).
    """
    client = anthropic.Anthropic(api_key=api_key)
    results: list[tuple[BatchItem, InvoiceData | None, str | None]] = []

    for result in client.messages.batches.results(batch_id):
        item = id_map[result.custom_id]

        if result.result.type == "succeeded":
            msg = result.result.message
            raw = next((b.text for b in msg.content if b.type == "text"), "")
            try:
                invoice = _parse_invoice_json(raw, msg.usage, model)
                results.append((item, invoice, None))
            except Exception as exc:
                results.append((item, None, str(exc)))
        else:
            err = getattr(result.result, "error", result.result.type)
            results.append((item, None, str(err)))

    return results
