"""fixar_fuso: o servidor roda em UTC; o processo precisa seguir Sao Paulo."""

from __future__ import annotations

import os
import time

from commons import datas


def test_fixa_tz_e_chama_tzset(monkeypatch):
    chamadas = []
    monkeypatch.delenv("TZ", raising=False)
    monkeypatch.setattr(time, "tzset", lambda: chamadas.append(True), raising=False)
    datas.fixar_fuso()
    assert os.environ["TZ"] == "America/Sao_Paulo"
    assert chamadas == [True]


def test_sem_tzset_nao_levanta(monkeypatch):
    monkeypatch.delattr(time, "tzset", raising=False)
    datas.fixar_fuso()
    assert os.environ["TZ"] == "America/Sao_Paulo"
