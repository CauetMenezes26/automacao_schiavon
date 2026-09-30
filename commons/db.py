"""Conexão com o PostgreSQL e leitura de profile.

Toda falha sai classificada: profile ausente ou malformado é
`ConfigException` (repetir não resolve), erro do driver é `DataAccessException`.

Este módulo é ferramenta (`commons`): conhece só `ConfigBanco`, o pedaço de
config que a conexão precisa. Quem monta o objeto de config completo, tipado,
a partir do profile é `domain/config.py` — a seta vai de `domain` para
`commons`, nunca o contrário.
"""

from __future__ import annotations

import io
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

import psycopg2
from dotenv import dotenv_values
from dotenv.parser import parse_stream

from .exception import ConfigException, DataAccessException
from .logging_config import get_logger

log = get_logger(__name__)


@dataclass(frozen=True)
class ConfigBanco:
    """Parâmetros de conexão. A senha fica fora do `repr` para não vazar em
    log nem em traceback."""

    host: str
    port: str
    database: str
    user: str
    password: str = field(repr=False)
    schema: str = "public"


def load_env(path: Path) -> dict[str, str]:
    """Lê um profile `KEY=VALUE` e devolve um dicionário chave -> valor.

    Parser do `python-dotenv`, estrito no separador: só `=`. O parser caseiro
    anterior aceitava `:` também e cortava no primeiro que achasse, então um
    valor como `URL=https://host:8080` numa linha sem cuidado, ou `SENHA:abc`,
    virava outra chave em silêncio. Linha que não é comentário nem `KEY=VALUE`
    agora é erro, com o número da linha — nunca o conteúdo, que pode ser
    segredo.

    Profile ausente ou ilegível é `ConfigException` e diz qual caminho o robô
    esperava.
    """
    try:
        conteudo = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigException(
            f"profile nao encontrado ou ilegivel em {path}; crie-o a partir de "
            "resources/config.example.env e preencha as credenciais."
        ) from exc

    # Valida com o proprio parser do dotenv: valor entre aspas que ocupa varias
    # linhas (private key, JSON de credencial) e valido, e suas continuacoes
    # nao tem `=` — uma checagem linha a linha as marcaria como malformadas.
    invalidas = [
        b.original.line for b in parse_stream(io.StringIO(conteudo)) if b.error
    ]
    if invalidas:
        raise ConfigException(
            f"profile {path.name} malformado: linha(s) {invalidas} fora do "
            "formato CHAVE=VALOR."
        )

    # `interpolate=False`: um `${...}` dentro de uma senha nao pode ser expandido.
    valores = dotenv_values(dotenv_path=path, interpolate=False)
    return {k: (v or "") for k, v in valores.items()}


def connect_db(cfg: ConfigBanco) -> psycopg2.extensions.connection:
    """Abre conexão com o PostgreSQL.

    Falha do driver é `DataAccessException`, com host/banco/usuário no texto
    (nunca a senha). Config incompleta é barrada antes, ao montar o
    `ConfigBanco` em `domain/config.py`, para não se disfarçar de erro de rede.
    """
    try:
        return psycopg2.connect(
            host=cfg.host,
            port=cfg.port,
            dbname=cfg.database,
            user=cfg.user,
            password=cfg.password,
            options=f"-c search_path={cfg.schema}",
            sslmode="require",
        )
    except psycopg2.Error as exc:
        raise DataAccessException(
            f"falha ao conectar em {cfg.host}:{cfg.port}/{cfg.database} "
            f"como {cfg.user}: {exc}"
        ) from exc


@contextmanager
def conexao(cfg: ConfigBanco) -> Iterator[psycopg2.extensions.connection]:
    """Conexão que fecha sozinha ao sair do `with`.

    O padrão "abre, usa, fecha" estava reescrito em nove módulos, cada um com
    seu próprio `_fechar` e seu próprio `try/finally` — e era de onde vinha a
    maior parte das funções com mais de um `try` (governança: um `try` por
    função, limpeza em auxiliar que nunca levanta). Com
    `with conexao(config.banco) as conn:` quem chama não escreve `try/finally` nenhum,
    e o `close` que nunca levanta mora aqui, num lugar só.

    Não faz commit nem rollback: quem decide a transação são os módulos de
    `domain/service/`, que já commitam por operação. Isto cuida só do
    fechamento.
    """
    conn = connect_db(cfg)
    try:
        yield conn
    finally:
        fechar(conn)


def reverter(conn) -> None:
    """Desfaz a transação aberta sem nunca levantar.

    Chamada quando um comando falhou: sem o rollback a conexão fica em
    transação abortada e TODO comando seguinte nela falha também.
    """
    try:
        conn.rollback()
    except Exception:  # noqa: BLE001 — ver docstring
        log.warning("db: falha no rollback", exc_info=True)


def fechar(conn) -> None:
    """Fecha a conexão sem nunca levantar.

    Chamada do `finally`, onde uma exceção nova SUBSTITUIRIA o erro real que
    está subindo — e aí o traceback mostra "falha ao fechar" em vez da causa.
    Aceita `None` para o chamador que não chegou a conectar.
    """
    if conn is None:
        return
    try:
        conn.close()
    except Exception:  # noqa: BLE001 — ver docstring
        log.warning("db: falha ao fechar a conexao", exc_info=True)
