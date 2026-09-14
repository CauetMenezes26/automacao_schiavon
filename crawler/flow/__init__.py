"""Fluxos do pipeline. Um modulo por fluxo: `<nome>_flow.py` expoe `<nome>_flow(env)`.

Contrato (ver `.cursor/rules/templates-rpa.mdc`, `tratamento-erro.mdc`,
`monitoramento.mdc`):
* `BusinessException` -> log WARNING, retorna normal (o `Pipeline` marca OK).
* Erro tecnico -> propaga para `Pipeline.rodar`, que loga o traceback e marca ERRO.
* Limpeza de recurso no `finally`, em funcao que nunca levanta.
* Acesso a sistema externo -> `monitor.registrar_acesso(conn, sistema, ok=...)`
  no ponto do login (NAO ha healthcheck neste projeto). O FLUXO 5 consolida.
"""
