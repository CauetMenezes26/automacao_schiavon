"""Classifica a linha lida da invoice: mercadoria, encargo ou ajuste.

O Vision extrai tudo que parece linha da nota, inclusive frete, taxa de combustível
e linhas de totalização. Nenhuma delas é comparável com uma cotação, e somá-las
infla o valor: na nota 60388642 do Cheney, as linhas somavam US$ 2.184,58 contra
US$ 2.177,58 no cabeçalho, porque 'Fuel Surcharge' e 'Total Discount/Surcharge'
eram a mesma cobrança de US$ 7,00 lida duas vezes.

Classificar em vez de descartar: a linha continua registrada — senão a diferença
some sem explicação — mas fica fora da conciliação e das somas de mercadoria.

A correspondência é por FRASE, nunca por token solto. Medido sobre as 1.480 linhas
já lidas, um `fee` avulso casa com Cof-fee, BIOFEEL e 'Beef, Feet'; um `total`
avulso casa com 'LOLA MORTE SUBITA REPARACAO TOTAL SPRAY'.
"""

from __future__ import annotations

import re

ITEM = "item"
ENCARGO = "encargo"
AJUSTE = "ajuste"

# Frete, combustível e taxas de entrega.
_ENCARGO = re.compile(
    r"(fuel\s+(sur)?charge"
    r"|fuel\s+delivery\s+charge"
    r"|chgs?\s+for\s+fuel"
    r"|delivery\s+(sur)?charge"
    r"|misc\s+charges"
    r"|freight)",
    re.I,
)

# Descontos e linhas de subtotal, que repetem valores já contados.
_AJUSTE = re.compile(
    r"(total\s+discount"
    r"|discount\s+sales"
    r"|sub\s*total)",
    re.I,
)


def tipo_linha(descricao: str | None) -> str:
    """Retorna 'item', 'encargo' ou 'ajuste'. Na dúvida, 'item'.

    Errar para 'item' é o lado seguro: um encargo classificado como mercadoria
    aparece como divergência e alguém confere; uma mercadoria classificada como
    encargo sai silenciosamente da conciliação.
    """
    if not descricao:
        return ITEM
    texto = descricao.strip()
    if _ENCARGO.search(texto):
        return ENCARGO
    if _AJUSTE.search(texto):
        return AJUSTE
    return ITEM


def eh_mercadoria(descricao: str | None) -> bool:
    """Atalho: True quando a linha deve entrar na conciliação e nas somas."""
    return tipo_linha(descricao) == ITEM
