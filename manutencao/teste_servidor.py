"""Teste ponta a ponta no servidor: confere se o ambiente fala com tudo.

    python -m manutencao.teste_servidor            # verificacoes seguras
    python -m manutencao.teste_servidor --email    # + envia e-mail de teste
    python -m manutencao.teste_servidor --login    # + login real SharePoint/Catapult

Somente leitura: nao grava no banco nem no monitor. Sai com codigo 1 se
qualquer verificacao falhar. Spec: .claude/rules/spec-teste-servidor.md.
"""

from __future__ import annotations

import argparse
import smtplib
import ssl
import sys
from collections.abc import Callable
from dataclasses import dataclass

from commons.datas import fixar_fuso
from commons.db import conexao
from commons.logging_config import configurar_logs, get_logger
from commons.paths import FILES_DIR, ROOT
from domain.config import Config, carregar_config

log = get_logger(__name__)

OK, FALHA, PULADA = "OK", "FALHA", "PULADA"
_TIMEOUT_S = 30


@dataclass(frozen=True)
class Resultado:
    nome: str
    status: str
    detalhe: str = ""


class _Pulada(Exception):
    """Pre-condicao ausente: a verificacao nao se aplica (nao e falha)."""


def _executar(nome: str, fn: Callable[[], str]) -> Resultado:
    """Roda uma verificacao. Rede de seguranca: nada aqui derruba as demais."""
    try:
        return Resultado(nome, OK, fn() or "")
    except _Pulada as exc:
        return Resultado(nome, PULADA, str(exc))
    except Exception as exc:  # noqa: BLE001 — uma falha nao impede as outras
        log.error("teste_servidor: %s falhou: %s", nome, exc)
        return Resultado(nome, FALHA, f"{type(exc).__name__}: {exc}"[:300])


# ---------------------------------------------------------------------------
# verificacoes
# ---------------------------------------------------------------------------

def _pastas() -> str:
    for base in (FILES_DIR, ROOT / "logs"):
        base.mkdir(parents=True, exist_ok=True)
        marca = base / ".teste_servidor"
        marca.write_text("ok", encoding="utf-8")
        marca.unlink()
    return "files/ e logs/ gravaveis"


def _postgres(config: Config) -> str:
    with conexao(config.banco) as conn, conn.cursor() as cur:
        cur.execute("SELECT 1, current_schema()")
        _, schema = cur.fetchone()
    return f"conectou, schema={schema}"


def _chromium() -> str:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        try:
            page = browser.new_page()
            page.goto("data:text/html,<title>ok</title>")
            titulo = page.title()
        finally:
            browser.close()
    return f"chromium abriu pagina (title={titulo})"


def _claude(config: Config) -> str:
    if not config.vision.api_key:
        raise _Pulada("schiavon_key_vision ausente")
    import anthropic

    client = anthropic.Anthropic(api_key=config.vision.api_key, timeout=_TIMEOUT_S)
    modelos = client.models.list(limit=1)
    return f"API respondeu ({len(modelos.data)} modelo)"


def _sheets(config: Config) -> str:
    if not config.sinonimos_sheet_id:
        raise _Pulada("SINONIMOS_SHEET_ID ausente")
    from commons.sheets import _service

    meta = _service().spreadsheets().get(
        spreadsheetId=config.sinonimos_sheet_id, fields="properties.title",
    ).execute()
    return f"planilha '{meta['properties']['title']}'"


def _gmail() -> str:
    from commons.gmail import _service

    perfil = _service().users().getProfile(userId="me").execute()
    return f"token valido ({perfil.get('emailAddress', '?')})"


def _smtp(config: Config) -> str:
    faltando = config.smtp.faltando()
    if faltando:
        raise _Pulada(f"profile sem {', '.join(faltando)}")
    s = config.smtp
    if int(s.port) == 465:
        server = smtplib.SMTP_SSL(
            s.host, 465, timeout=_TIMEOUT_S, context=ssl.create_default_context())
    else:
        server = smtplib.SMTP(s.host, int(s.port), timeout=_TIMEOUT_S)
        server.starttls()
    with server:
        server.login(s.usuario, s.senha)
    return f"autenticou em {s.host}:{s.port}"


def _ecrs(config: Config) -> str:
    e = config.ecrs
    faltam = [n for n, v in (
        ("ECRS_USER", e.usuario), ("ECRS_PASSWORD", e.senha),
        ("ECRS_WINDERMERE", e.url_windermere), ("ECRS_DRPHILIPS", e.url_drphilips),
    ) if not v]
    if faltam:
        raise _Pulada(f"profile sem {', '.join(faltam)}")
    return "credenciais e URLs presentes"


def _email(config: Config) -> str:
    from commons.email_client import enviar_email

    destino = config.alerta_email or "dataguvi@gmail.com"
    res = enviar_email(
        config.smtp, destino, f"[RPA Schiavon] TESTE de servidor - {config.ambiente}",
        "<p>E-mail de teste do <code>manutencao.teste_servidor</code>.</p>")
    if res["status"] != "sent":
        raise RuntimeError(res.get("error", "erro desconhecido"))
    return f"enviado para {destino}"


def _login_sharepoint(config: Config) -> str:
    from commons.sharepoint import open_sharepoint_session
    from domain.service.invoice_service import fetch_all_configs

    with conexao(config.banco) as conn:
        registros = fetch_all_configs(conn)
    if not registros:
        raise _Pulada("dim_loja sem configuracao de SharePoint")
    cred = config.sharepoint
    pw, browser, _, _ = open_sharepoint_session(
        cred.usuario, cred.senha, registros[0]["url"], headless=True)
    browser.close()
    pw.stop()
    return f"login ok (loja id={registros[0]['id']})"


def _login_catapult(config: Config) -> str:
    from commons.catapult import open_catapult_session

    e = config.ecrs
    if not (e.usuario and e.url_windermere):
        raise _Pulada("ECRS_USER/ECRS_WINDERMERE ausente")
    pw, browser, _ = open_catapult_session(
        e.url_windermere, e.access_email, e.usuario, e.senha, headless=True)
    browser.close()
    pw.stop()
    return "login ok (Windermere)"


# ---------------------------------------------------------------------------
# orquestracao
# ---------------------------------------------------------------------------

def rodar(config: Config, email: bool = False, login: bool = False) -> list[Resultado]:
    checks: list[tuple[str, Callable[[], str]]] = [
        ("Pastas", _pastas),
        ("PostgreSQL", lambda: _postgres(config)),
        ("Chromium (Playwright)", _chromium),
        ("Claude Vision", lambda: _claude(config)),
        ("Google Sheets", lambda: _sheets(config)),
        ("Gmail OAuth", _gmail),
        ("SMTP (login)", lambda: _smtp(config)),
        ("ECRS (profile)", lambda: _ecrs(config)),
    ]
    if email:
        checks.append(("E-mail de teste", lambda: _email(config)))
    if login:
        checks += [("Login SharePoint", lambda: _login_sharepoint(config)),
                   ("Login Catapult", lambda: _login_catapult(config))]
    return [_executar(nome, fn) for nome, fn in checks]


def _imprimir(config: Config, resultados: list[Resultado]) -> None:
    print(f"\nTeste de servidor - ambiente={config.ambiente}")
    for r in resultados:
        print(f"  [{r.status:6}] {r.nome:24} {r.detalhe}")
    falhas = sum(r.status == FALHA for r in resultados)
    print("\nRESULTADO:", "TUDO OK" if not falhas else f"{falhas} FALHA(S)")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--email", action="store_true", help="envia e-mail de teste")
    parser.add_argument("--login", action="store_true", help="login real SharePoint/Catapult")
    args = parser.parse_args()

    fixar_fuso()
    configurar_logs()
    config = carregar_config()
    resultados = rodar(config, email=args.email, login=args.login)
    _imprimir(config, resultados)
    sys.exit(1 if any(r.status == FALHA for r in resultados) else 0)


if __name__ == "__main__":
    main()
