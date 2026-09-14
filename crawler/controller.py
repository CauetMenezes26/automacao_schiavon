"""Controller — orquestração do pipeline.

`executar()` roda os cinco fluxos em sequência, sempre. Não há flags: o recorte
por fluxo e os atalhos avulsos saíram daqui; quem precisa de um passo isolado
chama a fachada do módulo. O que cada fluxo faz mora na sua fachada; aqui só a
ordem.

    FLUXO 1/5  Sincroniza o de-para de sinônimos de item (planilha -> banco)
    FLUXO 2/5  Coleta e leitura de invoices (SharePoint -> Vision -> banco)
    FLUXO 3/5  Cotação semanal de carnes (um passo do ciclo)
    FLUXO 4/5  Conciliação cotação x invoice (compara e grava)
    FLUXO 5/5  Monitor — consolida status dos sistemas e alerta a operação

Fino de propósito: monta o `Pipeline` (`crawler/pipeline.py`) e chama uma
fachada por fluxo. Sem argparse, sem recorte. As fachadas de fluxo migram para
`crawler/flow/` na Fase 3; até lá são importadas dos módulos de etapa.
"""

from __future__ import annotations

from commons.banner import imprimir_banner
from commons.db import connect_db, load_env
from commons.paths import ENV_PATH
from crawler.flow.conciliacao_flow import conciliacao_flow
from crawler.flow.cotacao_flow import cotacao_flow
from crawler.flow.invoices_flow import invoices_flow
from crawler.flow.monitor_flow import monitor_flow
from crawler.flow.painel_flow import painel_flow
from crawler.flow.sinonimos_flow import sinonimos_flow
from crawler.pipeline import Pipeline
from domain.service import sistema_service
from domain.service.agendamento_service import registrar_heartbeat

TOTAL_FLUXOS = 6

# FLUXO 6 — Conciliação com o ERP Catapult: acessar o ERP, raspar os dados e
# comparar com a nota.
#
# NÃO é um fluxo secundário, apesar de ainda não existir: é o único que vale
# para TODA nota. O desenho é
#
#     fornecedor de carne   ->  ERP  +  cotação semanal
#     qualquer outro        ->  ERP
#
# `dim_fornecedor.categoria` é quem faz esse roteamento (ver domain/categorias.py).
# O modelo já está pronto para receber: `fat_conciliacao` tem
# UNIQUE (id_invoice, comparacao) com comparacao IN ('cotacao','erp'), e
# commons/matcher.py nasceu sem dependência do projeto para ser reaproveitado
# aqui. Ligar quando o fluxo existir.
CONCILIAR_ERP_ATIVO = False


def executar() -> None:
    imprimir_banner()
    env = load_env(ENV_PATH)
    pipeline = Pipeline(TOTAL_FLUXOS)

    # registra em dim_sistema quais sistemas têm credencial no .env.
    # O resultado real de login durante os fluxos sobrescreve isto.
    sistema_service.checar_ambiente(env)

    # ------------------------------------------------------------------
    # FLUXO 1/5 -- Sincroniza o de-para de sinônimos de item
    # Lê files/sinonimos/*.xlsx e faz upsert em dim_item_sinonimo, para a
    # conciliação rodar com o vocabulário atualizado. Planilha ausente ou
    # travada no Excel -> pula e retoma na próxima execução.
    # ------------------------------------------------------------------
    pipeline.rodar(1, "Sinônimos", lambda: sinonimos_flow(env))

    # ------------------------------------------------------------------
    # FLUXO 2/5 -- Coleta e leitura de invoices
    # Baixa os PDFs da semana no SharePoint, manda o Claude Vision ler e
    # grava em fat_invoice / fat_invoice_item.
    # ------------------------------------------------------------------
    pipeline.rodar(2, "Invoices", lambda: invoices_flow(env))

    # ------------------------------------------------------------------
    # FLUXO 3/5 -- Cotação semanal de carnes
    # Avança UM passo do ciclo: abre a semana, ou verifica respostas e cobra
    # quem está atrasado. Não bloqueia.
    # ------------------------------------------------------------------
    pipeline.rodar(3, "Cotação", lambda: cotacao_flow(env))

    # ------------------------------------------------------------------
    # FLUXO 4/5 -- Conciliação cotação x invoice
    # Compara o preço faturado com o cotado na semana da invoice e grava em
    # fat_conciliacao / _item. Lê do banco, então roda mesmo que a coleta
    # acima tenha falhado.
    # ------------------------------------------------------------------
    pipeline.rodar(4, "Conciliação", lambda: conciliacao_flow(env))

    # ------------------------------------------------------------------
    # FLUXO 5/5 -- Monitor
    # Consolida o status de acesso dos sistemas e abre/fecha alertas à
    # operação (e-mail à GUVI, com dedupe). Lê o que os fluxos acima
    # deixaram; não depende de nenhum ter dado certo.
    # ------------------------------------------------------------------
    pipeline.rodar(5, "Monitor", lambda: monitor_flow(env))

    # ------------------------------------------------------------------
    # FLUXO 6/6 -- Relatorio Cotacao x Invoice
    # Consolida a comparacao de preco da semana corrente em duas abas de
    # files/relatorios/painel_operacao.xlsx (cotacao x invoice item a item, e
    # so as divergencias) - o relatorio que o cliente abre. So le do banco;
    # roda mesmo que os fluxos acima tenham falhado.
    # ------------------------------------------------------------------
    pipeline.rodar(6, "Painel", lambda: painel_flow(env))

    # Heartbeat do agendamento: registra esta execução e projeta a próxima
    # pela cron. Antes do resumo() porque ele sai com código 1.
    try:
        conn = connect_db(env)
        try:
            registrar_heartbeat(conn, "pipeline", "erro" if pipeline.houve_erro else "ok")
        finally:
            conn.close()
    except Exception as exc:
        print(f" heartbeat não gravado: {exc}")

    pipeline.resumo()
