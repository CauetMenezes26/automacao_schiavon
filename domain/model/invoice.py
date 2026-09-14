"""Schema da invoice — o que o Claude Vision extrai do PDF.

As descrições dos campos não são documentação: vão no prompt de extração
estruturada, então mudá-las muda o que o modelo devolve.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class InvoiceItem(BaseModel):
    item_order: int | None = None
    description: str | None = None
    quantity: float | None = None
    unit: str | None = None
    unit_price: float | None = None
    total_price: float | None = None
    handwritten_notes: str | None = Field(
        None,
        description="Anotações manuais em caneta: devoluções, ajustes, correções de preço/quantidade"
    )


class InvoiceData(BaseModel):
    # ── Invoice ──────────────────────────────────────────────────────────
    invoice_number: str | None = None
    invoice_date: str | None = Field(None, description="ISO 8601: YYYY-MM-DD")
    due_date: str | None = Field(None, description="ISO 8601: YYYY-MM-DD")
    currency: str | None = "USD"
    subtotal: float | None = None
    tax_amount: float | None = None
    total_amount: float | None = None

    # ── Fornecedor (Supplier / From) ──────────────────────────────────────
    supplier_name: str | None = None
    supplier_address: str | None = None
    supplier_phone: str | None = None
    supplier_email: str | None = None
    supplier_tax_id: str | None = None

    # ── Cliente (Bill To / Ship To) ───────────────────────────────────────
    client_name: str | None = None
    client_address: str | None = None
    client_tax_id: str | None = None

    # ── Itens ─────────────────────────────────────────────────────────────
    items: list[InvoiceItem] = Field(default_factory=list)

    # ── Indicador de qualidade da leitura ─────────────────────────────────
    reading_confidence: float = Field(
        ...,
        ge=0,
        le=100,
        description=(
            "Confiança da leitura em % (0-100). "
            "90-100: tudo legível; 70-89: pequenas ambiguidades; "
            "50-69: partes ilegíveis ou muito manuscrito; 0-49: leitura comprometida."
        ),
    )
    reading_status: Literal["success", "partial", "failed"] = "success"
    reading_notes: str | None = Field(
        None,
        description=(
            "Observações sobre dificuldades, anotações manuais detectadas, "
            "valores incertos ou campos não encontrados."
        ),
    )

    # ── Metadados da chamada à API ─────────────────────────────────────────
    model_ai: str = "claude-opus-4-8"
    cost_read: float = Field(
        0.0,
        description="Custo em USD da chamada à API (input + output tokens)",
    )
