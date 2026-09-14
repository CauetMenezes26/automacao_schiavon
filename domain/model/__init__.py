"""Modelos de dados do projeto.

O que entra aqui: a forma dos dados como eles são extraídos e persistidos —
o schema Pydantic que o Claude Vision preenche a partir do PDF, e as linhas
de cotação lidas da planilha.

O que NÃO entra: os objetos de valor internos do motor de match
(`conciliacao/matcher.py`). Aquele módulo é deliberadamente livre de imports do
projeto, para rodar sem banco e ser reaproveitado pela frente ERP.
"""
