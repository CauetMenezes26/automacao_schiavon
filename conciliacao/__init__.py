"""Conciliação de documentos.

Etapa 3 do RPA. Hoje contém as regras da frente Cotação × Invoice:

    reconcile_quote.py  regras da nota: resolve o fornecedor, compara os itens
    sinonimos.py        leitura/validação da planilha DE-PARA + upsert

O motor puro está em `commons/matcher.py`; a orquestração do período em
`crawler/flow/conciliacao_flow.py`. A frente ERP × Invoice reaproveitará o
`matcher` quando for construída.
"""
