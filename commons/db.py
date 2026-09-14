"""Conexão com o PostgreSQL e leitura do .env."""

from __future__ import annotations

from pathlib import Path

import psycopg2

from .paths import ENV_PATH


def load_env(path: Path = ENV_PATH) -> dict[str, str]:
    """Lê o arquivo .env e retorna um dicionário chave→valor."""
    values: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            key, _, value = line.partition("=")
        elif ":" in line:
            key, _, value = line.partition(":")
        else:
            continue
        key = key.strip().strip('"\'')
        value = value.strip().strip('"\'')
        if key:
            values[key] = value
    return values


def connect_db(env: dict[str, str]) -> psycopg2.extensions.connection:
    """Abre conexão com o PostgreSQL usando as variáveis do .env."""
    return psycopg2.connect(
        host=env["HOST"],
        port=env["PORT"],
        dbname=env["DATABASE"],
        user=env["USER"],
        password=env["PASSWORD"],
        options=f"-c search_path={env.get('SCHEMA', 'public')}",
        sslmode="require",
    )
