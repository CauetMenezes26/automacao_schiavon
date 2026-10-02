"""Teste de envio de e-mail pela porta 465 (SMTP_SSL), com anexo no modo cliente.

Usa host/usuario/senha/remetente do profile ativo (RPA_ENV), mas ignora
`SMTP_PORT`: conecta direto em SSL na 465, util quando a 587 (STARTTLS) e
bloqueada pela rede/antivirus. Nao passa pelo `commons/email_client`.

    python -m manutencao.teste_envio_email            # ao cliente
    python -m manutencao.teste_envio_email --erro     # a DataGuvi (ALERTA_EMAIL)

* cliente: envia aos `DESTINATARIOS_CLIENTE` ativos (`domain/config.py`) com o
  relatorio de sucesso FAKE em anexo (se o .docx estiver aberto no Word e nao
  puder ser regerado, anexa o que ja existe).
* --erro: envia a `config.alerta_email` (default dataguvi@gmail.com), sem anexo.

Sai com codigo 1 se faltar chave de SMTP ou se o envio falhar.
"""

from __future__ import annotations

import argparse
import smtplib
import ssl
import sys
from datetime import datetime
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

from commons.docx_report import DocxReportError
from commons.logging_config import configurar_logs, get_logger
from commons.paths import SUCESSOS_ERP_DIR
from conciliacao.relatorio_sucesso import gerar_relatorio_sucesso_erp
from domain.config import DESTINATARIOS_CLIENTE, ConfigSmtp, carregar_config
from manutencao.teste_relatorio_sucesso import HEADER, RESULTADO

log = get_logger(__name__)

PORTA_SSL = 465
TIMEOUT_S = 30
_DEST_ERRO_PADRAO = "dataguvi@gmail.com"


def _anexo_fake() -> Path | None:
    """Gera o relatorio fake; se falhar (arquivo aberto), reaproveita o existente."""
    try:
        return gerar_relatorio_sucesso_erp(HEADER, RESULTADO)
    except DocxReportError:
        existentes = sorted(SUCESSOS_ERP_DIR.glob("sucesso_*.docx"))
        return existentes[0] if existentes else None


def _montar(
    smtp: ConfigSmtp, destino: str, assunto: str, html: str, anexo: Path | None,
) -> MIMEMultipart:
    msg = MIMEMultipart("mixed")
    msg["Subject"] = assunto
    msg["From"] = smtp.remetente
    msg["To"] = destino
    msg.attach(MIMEText(html, "html", "utf-8"))
    if anexo is not None:
        parte = MIMEApplication(anexo.read_bytes(), Name=anexo.name)
        parte["Content-Disposition"] = f'attachment; filename="{anexo.name}"'
        msg.attach(parte)
    return msg


def _enviar(
    smtp: ConfigSmtp, destino: str, assunto: str, html: str, anexo: Path | None = None,
) -> bool:
    """Envia por SMTP_SSL na 465. Falha vira log e False, nunca levanta."""
    msg = _montar(smtp, destino, assunto, html, anexo)
    try:
        with smtplib.SMTP_SSL(
            smtp.host, PORTA_SSL, timeout=TIMEOUT_S, context=ssl.create_default_context(),
        ) as server:
            server.login(smtp.usuario, smtp.senha)
            server.send_message(msg)
    except (smtplib.SMTPException, OSError) as exc:
        log.exception("falha ao enviar para %s pela porta %s: %s", destino, PORTA_SSL, exc)
        return False
    log.info("e-mail de teste enviado para %s (porta %s)", destino, PORTA_SSL)
    return True


def _html_cliente() -> str:
    return (
        "<h2>Teste de envio - DataGuvi</h2>"
        "<p>E-mail de teste do RPA Schiavon (com anexo, porta 465).</p>"
        f"<p>Enviado em {datetime.now():%d/%m/%Y %H:%M:%S}.</p>"
    )


def _html_erro() -> str:
    return (
        "<h2>Teste de e-mail de erro - RPA Schiavon</h2>"
        "<p><strong>LookupError</strong>: elemento nao encontrado no Catapult (TESTE)</p>"
        "<p><strong>-</strong>: falha ao acessar o BD (TESTE)</p>"
        f"<p>Enviado em {datetime.now():%d/%m/%Y %H:%M:%S}.</p>"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--erro", action="store_true",
                        help="testa o e-mail de erro em vez do e-mail ao cliente")
    args = parser.parse_args()

    configurar_logs()
    config = carregar_config()
    faltando = [c for c in config.smtp.faltando() if c != "SMTP_PORT"]
    if faltando:
        print(f"SMTP incompleto no profile: falta {', '.join(faltando)}")
        sys.exit(1)

    anexo = None
    if args.erro:
        destinos = [config.alerta_email or _DEST_ERRO_PADRAO]
        assunto, html = f"[RPA Schiavon] TESTE de erro - {config.ambiente}", _html_erro()
    else:
        destinos = list(DESTINATARIOS_CLIENTE)
        assunto, html = "[DataGuvi] TESTE de envio", _html_cliente()
        anexo = _anexo_fake()
        if anexo is None:
            print("Sem .docx para anexar (geracao falhou e a pasta esta vazia).")
            sys.exit(1)
        print(f"Anexo: {anexo.name}")

    print(f"Destinatarios: {', '.join(destinos)} (porta {PORTA_SSL})")
    ok = all([_enviar(config.smtp, d, assunto, html, anexo) for d in destinos])
    print("ENVIADO" if ok else "FALHOU (ver log)")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
