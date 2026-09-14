"""Camada `commons` — FERRAMENTA. Não sabe o que o robô faz.

Regra das setas (ver `.cursor/rules/estrutura-projeto.mdc`):

    crawler ──► commons          commons ──► (nada interno)
    crawler ──► domain           domain  ──► (nada interno)

`commons` não importa nada de `domain` nem de `crawler`. Se um módulo aqui
precisa de um Model, de um Enum de status ou de um Service para funcionar, ele
**não** é `commons` — é `crawler`.

Conteúdo desta camada: utilitários técnicos genéricos (conexão, paths, texto,
e-mail, log, exceções) e a infraestrutura que o robô *pilota* para conseguir
trabalhar (SharePoint, mensageria, Vision), cada uma no seu subpacote.
"""
