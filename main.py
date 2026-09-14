"""RPA Schiavon — entrypoint.

`python main.py` roda o pipeline completo, uma passada. A orquestração (ordem
dos fluxos, isolamento de falha, RESUMO, heartbeat) mora em
`crawler/controller.py`; aqui só o contorno de processo.
"""

from __future__ import annotations

import sys
import traceback

from crawler.controller import executar

if __name__ == "__main__":
    try:
        executar()
    except SystemExit:
        raise
    except Exception:
        traceback.print_exc()
        sys.exit(1)
