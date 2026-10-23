"""Exporta los datos del vault a un snapshot portable para el deploy.

El dashboard lee del vault de Obsidian (raiz local). En Railway/Cloudflare no hay
vault: se sube un snapshot comprimido (data_snapshot/snapshot.json.gz) y la app
lo usa cuando no encuentra la raiz de datos o BM_READONLY=1.

Uso:
    python export_snapshot.py
    python export_snapshot.py --out data_snapshot/snapshot.json.gz

Solo stdlib + lo que ya usa loader.py.
"""
import argparse
import gzip
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import loader  # noqa: E402
import sanitizer  # noqa: E402


def build_payload():
    loader.IGNORED.clear()
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "data_root": str(loader.ROOT),
        "censura": loader.load_censura(),
        "subagentes": loader.load_subagentes(),
        "multi": loader.load_multiambito(),
        "cibersec": loader.load_cibersec(),
        "scripts": loader.load_scripts(),
        "ignored": list(loader.IGNORED),
    }


def main():
    ap = argparse.ArgumentParser(description="Snapshot de benchmarks para deploy")
    ap.add_argument("--out", default=None,
                    help="Ruta de salida (por defecto snapshot.json.gz, "
                         "snapshot_public.json.gz con --public)")
    ap.add_argument("--force", action="store_true",
                    help="Escribir aunque la raiz de datos no exista")
    ap.add_argument("--public", action="store_true",
                    help="Modo deploy: alias para modelos descensurados, sin "
                         "respuestas crudas, sin fuentes, sin rutas locales")
    ap.add_argument("--registry", default=str(sanitizer.REGISTRY_FILE),
                    help="Fichero JSON de registro de modelos (local, no versionado)")
    args = ap.parse_args()

    if not loader.ROOT.exists() and not args.force:
        sys.exit(f"La raiz de datos no existe: {loader.ROOT}. "
                "Define OQ_DATA_ROOT o usa --force para escribir un snapshot vacio.")

    payload = build_payload()
    if args.public:
        reg = sanitizer.load_registry(args.registry)
        payload, dropped = sanitizer.sanitize(payload, reg)
        leaks = sanitizer.audit(payload, reg)
        if leaks:
            sys.exit(f"ABORTADO: {len(leaks)} problemas en el filtrado. "
                    "Revisa model_registry.json antes de publicar.")
        print("Campos recortados:", len(dropped))
    out = Path(args.out or ("data_snapshot/snapshot_public.json.gz"
                            if args.public else "data_snapshot/snapshot.json.gz"))
    out.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(out, "wt", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False)

    counts = {k: len(payload[k]) for k in ("censura", "subagentes", "multi", "cibersec", "scripts")}
    print(f"Snapshot escrito en {out} ({out.stat().st_size} bytes)")
    if args.public:
        print("Modo PUBLICO: sin respuestas crudas, sin fuentes, modelos descensurados aliasados.")
    print("Filas:", counts, "| ignorados:", len(payload["ignored"]))


if __name__ == "__main__":
    main()
