"""Regenera, filtra, compara y publica el sitio de datos (Cloudflare Pages).

Flujo: vault -> payload -> sanitizer -> auditoria -> diff -> confirmacion ->
git commit + push en el repo del sitio -> deploy con `deploy_pages.py` (wrangler).

Ojo: el `git push` NO despliega. El proyecto de Pages no tiene integracion Git
activa y su workflow de Actions no tiene secrets; el sitio se publica con la
subida directa de `deploy_pages.py`.

Uso:
    python publish.py --dry-run      # solo informe, no escribe nada
    python publish.py               # escribe y push
    python publish.py --yes         # sin pregunta (para el boton de la app)

La raiz del sitio esta en config.json -> "site_repo".
"""
import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import loader  # noqa: E402
import sanitizer  # noqa: E402

REPO = Path(__file__).resolve().parent


def load_config():
    p = REPO / "config.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


def build_public(reg):
    loader.IGNORED.clear()
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "censura": loader.load_censura(),
        "subagentes": loader.load_subagentes(),
        "multi": loader.load_multiambito(),
        "cibersec": loader.load_cibersec(),
        "scripts": loader.load_scripts(),
        "ignored": list(loader.IGNORED),
    }
    clean, dropped = sanitizer.sanitize(payload, reg)
    leaks = sanitizer.audit(clean, reg)
    if leaks:
        raise SystemExit(
            f"ABORTADO: {len(leaks)} problemas de filtrado. No se publica nada:\n"
            + "\n".join(f"  - {k}: {s}" for k, s in leaks[:10])
        )
    return clean, dropped


def model_set(d):
    names = set()
    for r in d.get("censura", []):
        names.add(r.get("server", ""))
    for r in d.get("multi", []):
        names.add(r.get("model", ""))
    for r in d.get("cibersec", []):
        names.add(r.get("model", ""))
    for r in d.get("subagentes", []):
        names.add(r.get("model", "") or r.get("agent", ""))
    return {n for n in names if n}


def summary(d):
    return {
        "censura": len(d.get("censura", [])),
        "subagentes": len(d.get("subagentes", [])),
        "multi": len(d.get("multi", [])),
        "cibersec": len(d.get("cibersec", [])),
        "scripts": d.get("scripts", {}).get("total", 0),
        "modelos": len(model_set(d)),
    }


def load_prev(site_dir):
    p = Path(site_dir) / "data.json"
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def make_diff(prev, new):
    if prev is None:
        return {"estado": "primera publicacion", "nuevos": sorted(model_set(new))}
    old_models, new_models = model_set(prev), model_set(new)
    old_s, new_s = summary(prev), summary(new)
    return {
        "estado": "actualizacion",
        "modelos_nuevos": sorted(new_models - old_models),
        "modelos_retirados": sorted(old_models - new_models),
        "cambios": {k: new_s[k] - old_s[k] for k in old_s if new_s[k] != old_s[k]},
        "generado": new.get("generated_at", ""),
    }


def git_publish(site_dir, message):
    site_dir = str(site_dir)
    for cmd in (["git", "-C", site_dir, "add", "data.json"],
                ["git", "-C", site_dir, "commit", "-m", message],
                ["git", "-C", site_dir, "push"]):
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode != 0:
            raise SystemExit(f"git fallo ({cmd[1:]}): {r.stderr.strip() or r.stdout.strip()}")
    return subprocess.run(["git", "-C", site_dir, "log", "--oneline", "-1"],
                          capture_output=True, text=True).stdout.strip()


def strip_generated(d):
    """Copia del payload sin la marca de tiempo (para comparar)."""
    if d is None:
        return None
    return {k: v for k, v in d.items() if k != "generated_at"}


def deploy_site(site):
    """Sube el sitio con deploy_pages.py. Devuelve (ok, salida)."""
    r = subprocess.run(
        [sys.executable, str(REPO / "deploy_pages.py"),
         "--site", str(site), "--project", "benchmarks",
         "--domain", "benchmarks.sammideblas.com", "--branch", "master"],
        capture_output=True, text=True)
    ok = r.returncode == 0
    return ok, (((r.stdout or "") + (r.stderr or "")).strip())


def publish_site(site, clean=None, diff=None, message=None):
    """Publica de verdad: escribe data.json, commit+push y deploy directo.

    Devuelve changed, commit, deploy_ok, deploy_log y error. No propaga fallos
    esperables (git, red, token): los reporta en el dict.
    """
    site = Path(site)
    res = {"changed": False, "commit": None, "deploy_ok": None,
           "deploy_log": "", "error": None, "diff": diff}
    if clean is None:
        reg = sanitizer.load_registry()
        clean, _ = build_public(reg)
    prev = load_prev(site)
    if diff is None:
        diff = make_diff(prev, clean)
    res["diff"] = diff
    res["summary"] = summary(clean)

    if prev is not None and strip_generated(prev) == strip_generated(clean):
        return res

    out = site / "data.json"
    out.write_text(json.dumps(clean, ensure_ascii=False), encoding="utf-8")
    msg = message or (
        f"datos {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M')} UTC, "
        f"{summary(clean)['modelos']} modelos")
    try:
        res["commit"] = git_publish(site, msg)
    except SystemExit as e:
        res["error"] = str(e)
        return res
    res["changed"] = True

    ok, log = deploy_site(site)
    res["deploy_ok"] = ok
    res["deploy_log"] = log[-3000:]
    return res


def main():
    ap = argparse.ArgumentParser(description="Publicar datos filtrados a Pages")
    ap.add_argument("--dry-run", action="store_true", help="Solo informe, no escribe")
    ap.add_argument("--yes", action="store_true", help="Sin confirmacion interactiva")
    ap.add_argument("--site", default=None, help="Ruta del repo del sitio")
    args = ap.parse_args()

    cfg = load_config()
    site = args.site or cfg.get("site_repo")
    if not site:
        sys.exit("Falta 'site_repo' en config.json (ruta del repo de Pages).")
    site = Path(site)
    if not site.exists():
        sys.exit(f"El repo del sitio no existe: {site}. Crealo antes.")

    reg = sanitizer.load_registry()
    clean, dropped = build_public(reg)
    prev = load_prev(site)
    diff = make_diff(prev, clean)

    print("== Informe de publicacion ==")
    print("Modelos publicados:", summary(clean)["modelos"])
    print("Filas:", {k: v for k, v in summary(clean).items() if k != "modelos"})
    print("Campos recortados:", len(dropped))
    print("Diff:", json.dumps(diff, ensure_ascii=False, indent=2))

    if args.dry_run:
        return

    if not args.yes:
        ans = input("Publicar? (s/N): ").strip().lower()
        if ans != "s":
            print("Cancelado. Nada se escribio en el repo del sitio.")
            return

    res = publish_site(site, clean=clean, diff=diff)
    if res.get("error"):
        sys.exit(res["error"])
    if not res["changed"]:
        print("Sin cambios en data.json: no se publica nada.")
        return
    print("Commit:", res["commit"])
    print("Deploy:", "OK" if res["deploy_ok"] else "FALLIDO")
    print(res["deploy_log"])


if __name__ == "__main__":
    main()
