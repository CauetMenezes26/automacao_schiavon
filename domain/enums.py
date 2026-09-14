"""Status do pipeline — a fonte da verdade, em código.

Substitui as tabelas de catálogo: o banco guarda o número (chave) e o nome
(valor); o significado mora aqui.

São **dois vocabulários**, de propósito:

    StatusExecEnum          ciclo de vida do CASO   -> processo.cod_status
    StatusConciliacaoEnum   veredito da COMPARAÇÃO  -> fat_conciliacao(_item).cod_status

Misturá-los era o que fazia `OK` significar duas coisas: "a última etapa passou"
e "o preço bate". Um processo termina; uma comparação dá um veredito.
"""

from __future__ import annotations

from enum import IntEnum, StrEnum

__all__ = [
    "StatusExecEnum",         # ciclo de vida do caso  -> processo.cod_status
    "StatusConciliacaoEnum",  # veredito da comparacao -> fat_conciliacao(_item)
    "EtapaEnum",              # passo do pipeline      -> processo.cod_etapa
    "EnvioCotacaoEnum",       # entrega do envio       -> fat_cotacao_envio.envio_status
    "estado_apos",            # status + percentual depois de uma etapa OK
    "estado_falha",           # status + percentual quando a etapa falha
]


# =============================================================================
# Ciclo de vida do caso
# =============================================================================

class StatusExecEnum(IntEnum):
    """Onde o caso está. Gravado em `processo.cod_status`.

    Faixas:
        0- 9  terminou
       10-19  em curso, ou parado à espera de algo externo
       20-29  encerrado sem completar, por falta de insumo
       50-59  erro técnico — só esta faixa é reprocessável
    """

    # --- 0-9: terminou
    FINALIZADO = 0
    FINALIZADO_COM_ALERTA = 1      # concluiu, mas há item para conferir

    # --- 10-19: em curso
    PENDENTE = 10                  # criado, nenhuma etapa rodou
    EM_ANDAMENTO = 11              # alguma etapa concluída, faltam outras
    AGUARDANDO_RESPOSTA = 12       # parado à espera do fornecedor

    # --- 20-29: encerrado sem completar
    ENCERRADO_SEM_COTACAO = 20     # não havia cotação da semana para comparar
    ENCERRADO_SEM_ARQUIVO = 21     # a pasta da semana existe, mas está vazia

    # --- 50-59: erro técnico
    ERRO_LOGIN = 50
    ERRO_NAVEGACAO = 51
    ERRO_LEITURA = 52
    ERRO_API = 53
    ERRO_BAIXA_CONFIANCA = 54
    ERRO_SEM_FORNECEDOR = 55
    # Não é erro: é uma marca de "reconciliar de novo". Fica na faixa 50-59 de
    # propósito, para entrar na fila de reprocesso (cod_status BETWEEN 50 AND 59)
    # quando um sinônimo ou alias novo pode ter destravado a nota.
    REPROCESSAR_CONCILIACAO = 56

    @property
    def categoria(self) -> str:
        if self < 10:
            return "finalizado"
        if self < 20:
            return "em_curso"
        if self < 30:
            return "encerrado"
        return "erro"

    @property
    def reprocessavel(self) -> bool:
        """Só erro técnico volta para a fila sozinho.

        `ENCERRADO_SEM_COTACAO` não: rodar de novo sem a cotação da semana
        devolve o mesmo resultado. `ERRO_SEM_FORNECEDOR` sim, porque basta
        cadastrar o alias e o caso anda.
        """
        return 50 <= self <= 59

    @property
    def descricao(self) -> str:
        return _DESC_EXEC[self]


_DESC_EXEC: dict[StatusExecEnum, str] = {
    StatusExecEnum.FINALIZADO: "Todas as etapas concluídas.",
    StatusExecEnum.FINALIZADO_COM_ALERTA: "Concluído, mas há item marcado para revisão.",
    StatusExecEnum.PENDENTE: "Criado, nenhuma etapa executada ainda.",
    StatusExecEnum.EM_ANDAMENTO: "Alguma etapa concluída, faltam outras.",
    StatusExecEnum.AGUARDANDO_RESPOSTA: "Planilha enviada, fornecedor não preencheu.",
    StatusExecEnum.ENCERRADO_SEM_COTACAO: "Sem cotação da semana para comparar.",
    StatusExecEnum.ENCERRADO_SEM_ARQUIVO: "Pasta da semana encontrada, mas vazia.",
    StatusExecEnum.ERRO_LOGIN: "Falha de autenticação na origem.",
    StatusExecEnum.ERRO_NAVEGACAO: "Pasta ou arquivo não encontrado na origem.",
    StatusExecEnum.ERRO_LEITURA: "A IA não conseguiu extrair o documento.",
    StatusExecEnum.ERRO_API: "Falha de rede ou de serviço externo.",
    StatusExecEnum.ERRO_BAIXA_CONFIANCA: "Leitura abaixo do piso de confiança.",
    StatusExecEnum.ERRO_SEM_FORNECEDOR: "Nome da nota não casou com nenhum alias.",
    StatusExecEnum.REPROCESSAR_CONCILIACAO:
        "Marcado para reconciliar de novo após correção de vocabulário/de-para.",
}


# =============================================================================
# Veredito da comparação
# =============================================================================

class StatusConciliacaoEnum(IntEnum):
    """Resultado de comparar uma linha faturada com a cotada.

    Faixas:
        0- 9  bate
       10-19  diverge no preço
       20-29  não dá para comparar
    """

    CONFERIDO = 0

    PRECO_ACIMA = 10               # faturado > cotado, além da tolerância
    PRECO_ABAIXO = 11              # faturado < cotado, além da tolerância

    SEM_REFERENCIA_ITEM = 20       # item da nota sem par na cotação
    UNIDADE_DIVERGENTE = 21        # preço por caixa x preço por libra

    @property
    def categoria(self) -> str:
        if self < 10:
            return "conferido"
        if self < 20:
            return "divergente"
        return "sem_comparacao"

    @property
    def descricao(self) -> str:
        return _DESC_CONC[self]


_DESC_CONC: dict[StatusConciliacaoEnum, str] = {
    StatusConciliacaoEnum.CONFERIDO: "Preço dentro da tolerância.",
    StatusConciliacaoEnum.PRECO_ACIMA: "Faturado acima do cotado, além da tolerância.",
    StatusConciliacaoEnum.PRECO_ABAIXO: "Faturado abaixo do cotado, além da tolerância.",
    StatusConciliacaoEnum.SEM_REFERENCIA_ITEM: "Item da nota sem par no lado cotado.",
    StatusConciliacaoEnum.UNIDADE_DIVERGENTE: "Preço por caixa não compara com preço por libra.",
}


# =============================================================================
# Entrega do envio da cotação
# =============================================================================

class EnvioCotacaoEnum(StrEnum):
    """O que aconteceu com o envio da planilha a um fornecedor.

    Gravado em `fat_cotacao_envio.envio_status`. Antes disto o dict que o Twilio
    devolve trazia `status` e o código o descartava — falha de envio de WhatsApp
    só saía num `print`.
    """

    ENFILEIRADO = "enfileirado"   # aceito pelo provedor, ainda não saiu
    ENVIADO = "enviado"           # provedor confirmou o envio (sent/accepted)
    ENTREGUE = "entregue"         # confirmação de entrega (delivered)
    FALHOU = "falhou"             # erro no envio (credencial, número, SMTP...)
    SEM_CANAL = "sem_canal"       # fornecedor sem WhatsApp nem e-mail cadastrado

    @property
    def descricao(self) -> str:
        return _DESC_ENVIO[self]

    @staticmethod
    def do_twilio(status: str | None) -> "EnvioCotacaoEnum":
        """Mapeia o status inicial do Twilio (queued/sent/accepted/...)."""
        s = (status or "").lower()
        if s in ("delivered",):
            return EnvioCotacaoEnum.ENTREGUE
        if s in ("failed", "undelivered", "canceled"):
            return EnvioCotacaoEnum.FALHOU
        if s in ("queued", "accepted", "scheduled"):
            return EnvioCotacaoEnum.ENFILEIRADO
        return EnvioCotacaoEnum.ENVIADO


_DESC_ENVIO: dict[EnvioCotacaoEnum, str] = {
    EnvioCotacaoEnum.ENFILEIRADO: "Aceito pelo provedor, aguardando envio.",
    EnvioCotacaoEnum.ENVIADO: "Provedor confirmou o envio.",
    EnvioCotacaoEnum.ENTREGUE: "Entrega confirmada pelo destinatário.",
    EnvioCotacaoEnum.FALHOU: "Erro no envio.",
    EnvioCotacaoEnum.SEM_CANAL: "Fornecedor sem canal de contato cadastrado.",
}


# =============================================================================
# Etapas
# =============================================================================

class EtapaEnum(IntEnum):
    """Passo do pipeline, gravado em `processo.cod_etapa`.

    A dezena identifica o fluxo, a unidade a ordem dentro dele — então
    `percent_exec` sai de uma divisão simples, sem catálogo.
    """

    # --- 1x: nota do fornecedor
    COLETAR = 11
    LER = 12
    IDENTIFICAR_FORNECEDOR = 13
    CONCILIAR_COTACAO = 14

    # --- 2x: ciclo de cotação
    ABRIR_CICLO = 21
    PUBLICAR_PLANILHA = 22
    NOTIFICAR = 23
    AGUARDAR_RESPOSTA = 24
    IMPORTAR_PRECOS = 25

    # --- 3x: varredura do SharePoint
    #
    # A coleta é um caso próprio, e não uma etapa da nota, porque ela existe
    # mesmo quando não acha arquivo nenhum. No dwschiavon isso era o
    # `execution_log`, e 47 das 148 linhas dele são "pasta não encontrada" —
    # se a navegação fosse etapa da nota, essas 47 falhas não teriam onde morar.
    NAVEGAR = 31
    BAIXAR = 32

    @property
    def fluxo(self) -> str:
        return {1: "invoice", 2: "cotacao", 3: "coleta"}[self // 10]

    @property
    def ordem(self) -> int:
        return self % 10

    @property
    def ultima(self) -> bool:
        return self.ordem == _ultima_ordem(self.fluxo)

    @property
    def percent_exec(self) -> int:
        """Quanto do fluxo está concluído quando esta etapa termina bem."""
        return round(self.ordem * 100 / _ultima_ordem(self.fluxo))


def _ultima_ordem(fluxo: str) -> int:
    return max(e.ordem for e in EtapaEnum if e.fluxo == fluxo)


# =============================================================================
# Derivação do estado
# =============================================================================

def estado_apos(etapa: EtapaEnum, com_alerta: bool = False) -> tuple[StatusExecEnum, int]:
    """Status e percentual do caso depois que `etapa` conclui com sucesso.

    É aqui que 'terminou' deixa de ser adivinhação: a última etapa do fluxo
    fecha o caso como FINALIZADO; qualquer outra deixa EM_ANDAMENTO.
    """
    if etapa.ultima:
        status = (StatusExecEnum.FINALIZADO_COM_ALERTA if com_alerta
                  else StatusExecEnum.FINALIZADO)
    else:
        status = StatusExecEnum.EM_ANDAMENTO
    return status, etapa.percent_exec


def estado_falha(etapa: EtapaEnum, status: StatusExecEnum) -> tuple[StatusExecEnum, int]:
    """Status e percentual quando `etapa` falha.

    O percentual é o da etapa ANTERIOR: a que falhou não conta como executada.
    """
    return status, round((etapa.ordem - 1) * 100 / _ultima_ordem(etapa.fluxo))
