"""Rutas del proyecto.

El repo vive fuera del vault (C:\\Users\\Sammi\\AI\\benchmarks-llm) y los informes de
benchmark siguen en Obsidian. Prioridad de la raiz de datos:
  1. env OQ_DATA_ROOT
  2. clave "data_root" en config.json
  3. default del vault

Si la raiz no existe, el dashboard muestra un aviso claro en vez de quedarse vacio
sin explicacion.
"""
import json
import os
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent
DEFAULT_DATA_ROOT = Path("C:/Users/Sammi/Documents/Destino/40-IA/ENTORNOS-IA/BENCHMARKS")


def data_root() -> Path:
    env = os.environ.get("OQ_DATA_ROOT", "").strip()
    if env:
        return Path(env)
    cfg = APP_DIR / "config.json"
    if cfg.exists():
        try:
            raw = json.loads(cfg.read_text(encoding="utf-8-sig"))
            r = str(raw.get("data_root", "")).strip()
            if r:
                return Path(r)
        except Exception:
            pass
    return DEFAULT_DATA_ROOT


ROOT = data_root()
