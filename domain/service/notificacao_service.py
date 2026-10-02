"""Notificacao por e-mail: relatorios ao cliente e erros a DataGuvi.

Ver `.claude/rules/spec-notificacao-email.md`.

  * `enviar_relatorios_cliente` - um e-mail por execucao, com os `.docx` gerados
    anexados, para `DESTINATARIOS_CLIENTE`.
  * `registrar_erro` / `enviar_erros` - os pontos de falha registram aqui (em
    memoria) e o controller manda UM e-mail consolidado, com traceback, a
    `config.alerta_email`. Nenhum laco de item dispara e-mail proprio.

Nada aqui levanta: notificacao e secundaria ao pipeline.
"""

from __future__ import annotations

import traceback
from dataclasses import dataclass
from datetime import datetime
from html import escape
from pathlib import Path

from commons.email_client import enviar_email
from commons.exception import ConfigException
from commons.logging_config import get_logger
from domain.config import DESTINATARIOS_CLIENTE, Config

log = get_logger(__name__)

__all__ = [
    "RelatorioNota", "registrar_erro", "enviar_erros", "enviar_relatorios_cliente",
    "limpar_erros", "LIMITE_ANEXO_BYTES",
]

_DEST_ERRO_PADRAO = "dataguvi@gmail.com"
_FMT_HORA = "%d/%m/%Y %H:%M:%S"


# O Outlook limita o e-mail a 25 MB e o anexo vai em base64 (~+33%): 18 MB de
# arquivo ja encosta no teto. Um .docx de invoice tem dezenas de KB; isto e so
# uma trava contra o imprevisto.
LIMITE_ANEXO_BYTES = 18 * 1024 * 1024


@dataclass(frozen=True)
class RelatorioNota:
    """Um relatorio `.docx` de uma invoice, pronto para ir ao cliente."""

    invoice: str
    fornecedor: str
    loja: str
    divergencia: bool          # True: relatorio de divergencia; False: de conciliacao OK
    caminho: Path
    itens: int
    itens_divergentes: int

    @property
    def titulo(self) -> str:
        return "Divergência" if self.divergencia else "Conciliação"

    def assunto(self) -> str:
        return f"[DataGuvi] {self.titulo} - Invoice {self.invoice} - {self.fornecedor} ({self.loja})"


@dataclass(frozen=True)
class _Erro:
    quando: str
    contexto: str
    tipo: str
    mensagem: str
    traceback: str


# Coletor do processo: uma execucao do pipeline por processo.
_ERROS: list[_Erro] = []


def limpar_erros() -> None:
    _ERROS.clear()


def registrar_erro(
    contexto: str, exc: BaseException | None = None, mensagem: str | None = None,
) -> None:
    """Guarda a falha para o e-mail consolidado. Nunca levanta.

    `contexto` diz ONDE (ex.: "FLUXO 3/5 Conciliacao ERP", "Catapult nota 123");
    `exc` traz tipo e traceback; `mensagem` cobre falha sem excecao.
    """
    tb = ""
    if exc is not None:
        tb = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    _ERROS.append(_Erro(
        quando=datetime.now().strftime(_FMT_HORA),
        contexto=contexto,
        tipo=type(exc).__name__ if exc is not None else "-",
        mensagem=mensagem or (str(exc) if exc is not None else ""),
        traceback=tb,
    ))


def enviar_erros(config: Config) -> bool:
    """Manda o e-mail consolidado das falhas registradas. Nunca levanta.

    True se enviou. Sem falhas registradas nao envia (e devolve False). Limpa o
    coletor ao fim, tenha enviado ou nao: reenviar o mesmo erro no proximo
    processo nao ajuda quem ja esta olhando o log.
    """
    erros = list(_ERROS)
    limpar_erros()
    if not erros:
        return False
    destino = config.alerta_email or _DEST_ERRO_PADRAO
    assunto = f"[RPA Schiavon] ERRO na execucao - {len(erros)} falha(s) - {config.ambiente}"
    enviou = _enviar(config, destino, assunto, _html_erros(erros, config.ambiente),
                     _txt_erros(erros), "e-mail de erro")
    if not enviou:
        # Sem canal para avisar: o log e o que sobra para quem for investigar.
        log.error("notificacao: %s falha(s) nao enviada(s) por e-mail:\n%s",
                  len(erros), _txt_erros(erros))
    return enviou


def enviar_relatorios_cliente(config: Config, relatorios: list[RelatorioNota]) -> int:
    """Envia ao cliente UM e-mail por invoice, cada um com o seu `.docx`. Nunca levanta.

    Separado por nota por causa do limite de 25 MB do Outlook: o anexo vai
    codificado (~+33%) e um e-mail unico com todos estouraria. O assunto
    distingue "Divergencia" de "Conciliacao". Falha numa nota nao impede as
    outras; vira erro registrado (e-mail de erro a DataGuvi). Os destinatarios
    de `DESTINATARIOS_CLIENTE` vao juntos no mesmo e-mail (adicionar gente nao
    multiplica mensagens). Devolve quantos e-mails de nota sairam.
    """
    if not relatorios:
        log.info("notificacao: nenhum relatorio gerado, e-mail ao cliente nao enviado")
        return 0
    enviados = sum(_enviar_nota(config, r) for r in relatorios)
    log.info("notificacao: %s/%s e-mail(s) ao cliente enviado(s)", enviados, len(relatorios))
    return enviados


def _enviar_nota(config: Config, rel: RelatorioNota) -> bool:
    """UM e-mail da invoice, com todos os destinatarios ativos juntos no Para."""
    anexo = rel.caminho
    if not anexo.is_file():
        registrar_erro(
            f"anexo ausente nota {rel.invoice}",
            mensagem=f"{anexo.name} nao existe; enviado sem anexo",
        )
        anexo = None
    elif anexo.stat().st_size > LIMITE_ANEXO_BYTES:
        registrar_erro(
            f"anexo grande demais nota {rel.invoice}",
            mensagem=f"{anexo.name} tem {anexo.stat().st_size} bytes; enviado sem anexo",
        )
        anexo = None
    html = _html_cliente(rel, anexado=anexo is not None)
    return _enviar(
        config, ", ".join(DESTINATARIOS_CLIENTE), rel.assunto(), html, None,
        f"e-mail da nota {rel.invoice}", [anexo] if anexo else None,
    )


def _enviar(
    config: Config, destino: str, assunto: str, html: str, txt: str | None,
    contexto: str, anexos: list[Path] | None = None,
) -> bool:
    """Um envio. Falha vira log (e-mail de erro) ou registro (e-mail ao cliente)."""
    try:
        res = enviar_email(config.smtp, destino, assunto, html, txt, anexos or ())
    except ConfigException as exc:
        return _falhou(contexto, destino, exc, str(exc))
    for nome in res.get("anexos_ignorados", ()):
        registrar_erro(f"anexo ignorado em {contexto}",
                       mensagem=f"{nome} nao pode ser lido; e-mail saiu sem ele")
    if res.get("status") == "sent":
        return True
    return _falhou(contexto, destino, None, res.get("error", "erro desconhecido"))


def _falhou(contexto: str, destino: str, exc: BaseException | None, motivo: str) -> bool:
    """Registra a falha de envio. O e-mail de erro que falha nao se registra de
    novo (viraria um laco: o proprio registro so sera enviado por esse canal)."""
    log.error("notificacao: %s para %s falhou - %s", contexto, destino, motivo)
    if contexto != "e-mail de erro":
        registrar_erro(f"falha ao enviar {contexto} ({destino})", exc, motivo)
    return False


# ---------------------------------------------------------------------------
# corpos
# ---------------------------------------------------------------------------

def _html_cliente(rel: RelatorioNota, anexado: bool) -> str:
    anexo = (f"Relatorio em anexo: {escape(rel.caminho.name)}" if anexado
             else "Relatorio nao anexado (arquivo ausente ou grande demais); solicite a DataGuvi.")
    return (
        "<html><body style='font-family:Arial,sans-serif;color:#333'>"
        f"<h2>{rel.titulo}</h2>"
        f"<p>Invoice: <strong>{escape(rel.invoice)}</strong><br>"
        f"Fornecedor: {escape(rel.fornecedor)}<br>Loja: {escape(rel.loja)}<br>"
        f"Itens conferidos: {rel.itens}<br>Itens com divergencia: {rel.itens_divergentes}</p>"
        f"<p>{anexo}</p>"
        "<hr><p style='font-size:.8em;color:#999'>DataGuvi - envio automatico.</p>"
        "</body></html>"
    )


def _html_erros(erros: list[_Erro], ambiente: str) -> str:
    blocos = "".join(
        f"<h3>{i}. {escape(e.contexto)}</h3>"
        f"<p>{escape(e.quando)} - <strong>{escape(e.tipo)}</strong>: "
        f"{escape(e.mensagem)}</p>"
        + (f"<pre style='background:#f4f4f4;padding:8px;font-size:12px'>"
           f"{escape(e.traceback)}</pre>" if e.traceback else "")
        for i, e in enumerate(erros, 1)
    )
    return (
        "<html><body style='font-family:Arial,sans-serif;color:#333'>"
        f"<h2>RPA Schiavon - {len(erros)} falha(s) ({escape(ambiente)})</h2>"
        f"{blocos}</body></html>"
    )


def _txt_erros(erros: list[_Erro]) -> str:
    return "\n\n".join(
        f"{i}. {e.contexto}\n{e.quando} {e.tipo}: {e.mensagem}\n{e.traceback}"
        for i, e in enumerate(erros, 1)
    )
