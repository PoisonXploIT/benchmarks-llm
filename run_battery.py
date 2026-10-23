# -*- coding: utf-8 -*-
"""run_battery.py - ejecuta una bateria en un PROCESO APARTE, independiente de Streamlit.

Por que: Streamlit reejecuta el script y recarga los modulos locales al recargar la pagina; una
bateria que corre dentro de su proceso se pierde/aborta. Lanzada como proceso propio, sobrevive a
recargas, a reruns y (mientras viva) a reinicios del servidor de Streamlit.

Escribe el log a stdout (la UI lo redirige a `run.log`) y el progreso a un JSON (`run-progress.json`).
Uso interno del dashboard (no pensado para uso manual, aunque admite argv).
"""
import argparse
import json
import sys
import time
from pathlib import Path

APP = Path(__file__).resolve().parent
sys.path.insert(0, str(APP))
import orchestrator as o  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--battery", required=True)
    ap.add_argument("--params", required=True)
    ap.add_argument("--progress", required=True)
    ap.add_argument("--dry", action="store_true")
    a = ap.parse_args()
    cfg = o.load_config()
    params = json.loads(Path(a.params).read_text(encoding="utf-8"))
    pf = Path(a.progress)

    def log(msg):
        print("[%s] %s" % (time.strftime("%H:%M:%S"), msg), flush=True)

    def prog(f):
        try:
            pf.write_text(json.dumps({"done": False, "progress": float(f), "ts": time.time()}),
                          encoding="utf-8")
        except Exception:
            pass

    prog(0.0)
    try:
        try:
            srv, started = o.start_server(cfg, log=log)
            if started and not a.dry:
                srv.wait_ready(timeout=1800)
        except Exception as e:  # noqa: BLE001
            log("no pude preparar el servidor: %s" % e)
        res = o.run_all(cfg, a.battery, params, log=log, progress=prog, dry_run=a.dry)
        pf.write_text(json.dumps({"done": True, "progress": 1.0,
                                  "summary": res.get("summary", ""),
                                  "paths": res.get("paths", [])}, ensure_ascii=False),
                      encoding="utf-8")
    except Exception as e:  # noqa: BLE001
        pf.write_text(json.dumps({"done": True, "progress": 1.0, "error": str(e)},
                                 ensure_ascii=False), encoding="utf-8")
        log("ERROR: %s" % e)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
