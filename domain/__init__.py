"""Camada `domain` — O DADO. O que o robô lê, grava e configura.

Regra das setas: `domain` nao importa `commons` nem `crawler`. Excecao tolerada:
`domain/service/*` usa `commons/db.py` e `commons/exception.py` (infra sem regra
de negocio). Ver `.cursor/rules/estrutura-projeto.mdc`.
"""
