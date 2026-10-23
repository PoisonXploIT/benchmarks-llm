"""Filtrado del snapshot antes de publicarlo (Cloudflare Pages).

Modo publico: solo metricas. Nunca el contenido de las respuestas.

Reglas:
  1. Alias para modelos descensurados (uc) u ofensivos (of). La correspondencia
     real esta en model_registry.json (local, gitignoreado) y en la nota del vault.
     Las cuantizaciones (UD, GSQ-RCO, FF711, PQ2_0...) NO se aliasan: son el base
     oficial cuantizado, verificado en los metadatos GGUF.
  2. Se eliminan las respuestas crudas (content, reasoning) y el fuente de scripts.
  3. Se eliminan _fuente / fuente / path / api / data_root: delatan rutas del vault.
  4. Los scripts se publican agregados (total, por lenguaje, lineas), no la lista.
  5. Ningun string del output supera max_string_len (200).
  6. Se audita: si sobrevive un alias, una ruta local o un veto, se aborta.

Uso:
    python export_snapshot.py --public
    python sanitizer.py --check data_snapshot/snapshot_public.json.gz
"""
import gzip
import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent
REGISTRY_FILE = REPO / "model_registry.json"

DROP_KEYS = {"_fuente", "fuente", "content", "reasoning", "stderrTail",
             "api", "data_root", "path", "name", "desc", "model_name"}
NAME_KEYS = {"model", "server", "agent", "title", "benchmark", "family"}
DEFAULTS = {
    "aliases": [],
    "no_alias": [],
    "artifacts": [],
    "max_string_len": 200,
    "veto": [],
}


def load_registry(path=REGISTRY_FILE):
    p = Path(path)
    if not p.exists():
        raise SystemExit(
            f"Falta {p}. Sin registro no se puede publicar: los modelos "
            "descensurados saldrian con su nombre real."
        )
    reg = dict(DEFAULTS)
    reg.update(json.loads(p.read_text(encoding="utf-8")))
    if not reg["aliases"]:
        raise SystemExit("El registro no tiene alias: nada que publicar.")
    return reg


def alias_name(value, reg):
    """Sustituye un identificador de modelo por su alias. Orden = especificidad."""
    if not isinstance(value, str) or not value:
        return value
    for a in reg["aliases"]:
        if re.search(a["regex"], value):
            return a["alias"]
    return value


def aggregate_scripts(scripts):
    by_lang = {}
    lines = 0
    for s in scripts:
        lang = s.get("language", "text")
        by_lang[lang] = by_lang.get(lang, 0) + 1
        lines += int(s.get("lines", 0) or 0)
    return {"total": len(scripts), "por_lenguaje": by_lang, "lineas_totales": lines}


def sanitize(payload, reg):
    max_len = int(reg["max_string_len"])
    artifacts = set(reg["artifacts"])
    dropped = []

    def is_artifact(obj):
        for key in ("model", "server"):
            if obj.get(key) in artifacts:
                return True
        return False

    def walk(obj, path=""):
        if isinstance(obj, dict):
            if is_artifact(obj):
                dropped.append(f"{path}: artefacto de parsing")
                return None
            out = {}
            for k, v in obj.items():
                k2 = alias_name(k, reg)   # nombres de columna tipo "heretic" tambien delatan
                if k in DROP_KEYS:
                    dropped.append(f"{path}.{k}")
                    continue
                if k in NAME_KEYS:
                    out[k2] = alias_name(v, reg)
                elif k == "scripts":
                    out[k2] = aggregate_scripts(v)
                else:
                    nv = walk(v, f"{path}.{k}")
                    if isinstance(nv, str) and len(nv) > max_len:
                        dropped.append(f"{path}.{k} ({len(nv)} chars)")
                        continue
                    if nv is not None:
                        out[k2] = nv
            return out
        if isinstance(obj, list):
            return [x for x in (walk(v, f"{path}[{i}]") for i, v in enumerate(obj)) if x is not None]
        if isinstance(obj, str):
            return alias_name(obj, reg)
        return obj

    clean = walk(payload)
    clean["mode"] = "public"
    clean["dropped_fields"] = len(dropped)
    return clean, dropped


def audit(payload, reg):
    """Devuelve [(problema, trozo)]. Vacio = se puede publicar."""
    hits = []
    blob = json.dumps(payload, ensure_ascii=False)
    for a in reg["aliases"]:
        m = re.search(a["regex"], blob)
        if m:
            hits.append(("nombre real de modelo", blob[max(0, m.start() - 30):m.end() + 30]))
    for v in reg["veto"]:
        m = re.search(v, blob)
        if m:
            hits.append(("veto", blob[max(0, m.start() - 20):m.end() + 20]))
    max_len = int(reg["max_string_len"])
    def walk(obj, path=""):
        if isinstance(obj, dict):
            for k, v in obj.items():
                walk(v, f"{path}.{k}")
        elif isinstance(obj, list):
            for i, v in enumerate(obj):
                walk(v, f"{path}[{i}]")
        elif isinstance(obj, str) and len(obj) > max_len:
            hits.append(("string demasiado largo", f"{path}: {len(obj)} chars"))
    walk(payload)
    return hits


def load_snapshot(path):
    p = Path(path)
    if p.suffix == ".gz":
        with gzip.open(p, "rt", encoding="utf-8") as f:
            return json.load(f)
    return json.loads(p.read_text(encoding="utf-8"))


DATA_EXT = {".json", ".md", ".txt", ".csv", ".gz", ".json.gz"}
SKIP_PREFIX = ("sanitizer.py", "publish.py", "tests/", "README.md")


def check_tree():
    """Escanea los ficheros de DATOS versionados buscando nombres reales de modelos.

    El codigo fuente se excluye a proposito: el sanitizer y sus tests contienen los
    patrones por definicion. Lo que no puede versionarse es un dato con el nombre real.
    """
    import subprocess
    reg = load_registry()
    files = subprocess.run(["git", "ls-files"], capture_output=True, text=True).stdout.split()
    hits = []
    for f in files:
        if f.startswith(SKIP_PREFIX) or Path(f).suffix not in DATA_EXT:
            continue
        try:
            raw = Path(f).read_bytes()
        except Exception:
            continue
        if raw.startswith(b"\x1f\x8b"):          # .gz: descomprimir antes de buscar
            try:
                import gzip
                raw = gzip.decompress(raw)
            except Exception:
                pass
        txt = raw.decode("utf-8", errors="replace")
        for a in reg["aliases"]:
            if re.search(a["regex"], txt):
                hits.append((f, a["alias"], a["regex"]))
    for f in files:
        if f.startswith("data_snapshot/"):
            hits.append((f, "snapshot de datos versionado", "data_snapshot/"))
    return hits


def main():
    if len(sys.argv) >= 2 and sys.argv[1] == "--check-tree":
        hits = check_tree()
        if hits:
            print("LEAK en ficheros versionados:")
            for f, alias, rx in hits[:20]:
                print("  ", f, "|", rx)
            sys.exit(1)
        print("OK: ningun fichero versionado contiene nombres reales de modelos.")
        return
    if len(sys.argv) >= 2 and sys.argv[1] == "--check":
        snap = sys.argv[2] if len(sys.argv) > 2 else "data_snapshot/snapshot_public.json.gz"
        reg = load_registry()
        hits = audit(load_snapshot(snap), reg)
        if hits:
            print(f"LEAK: {len(hits)} problemas en {snap}")
            for kind, snippet in hits[:20]:
                print("  ", kind, "|", snippet)
            sys.exit(1)
        print(f"OK: {snap} pasa el filtrado.")
        return
    print(__doc__)


if __name__ == "__main__":
    main()
