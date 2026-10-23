"""Sube el sitio de datos a Cloudflare Pages por API.

Requiere en el entorno:
    CLOUDFLARE_API_TOKEN   (scopes: Pages Edit, Zone Read, Access Read-Write)
    CLOUDFLARE_ACCOUNT_ID

Uso:
    python deploy_pages.py --site ../benchmarks-site \
        --project benchmarks --domain benchmarks.sammideblas.com \
        --access-email tu@email.com

Idempotente: si el proyecto existe, solo redeploya.
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

# Solo se publican assets: nada de README, zips, workflows ni dotfiles.
PUBLIC_SUFFIXES = {".html", ".js", ".css", ".json", ".svg", ".png", ".jpg",
                   ".jpeg", ".webp", ".ico", ".woff", ".woff2", ".txt"}

API = "https://api.cloudflare.com/client/v4"


def api(token, path, method="GET", payload=None, raw=None,
        content_type="application/json", strict=True):
    """Llamada a la API. Con strict=False devuelve el error en vez de abortar."""
    url = API + path
    data = raw if raw is not None else (json.dumps(payload).encode() if payload else None)
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", "Bearer " + token)
    if content_type:
        req.add_header("Content-Type", content_type)
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")
        if not strict:
            return {"__status": e.code, "success": False, "body": body[:400]}
        raise SystemExit(f"Cloudflare {e.code} en {path}: {body[:400]}")


def credentials():
    """Token y cuenta. Manda el registro de Windows; el entorno como respaldo.

    Streamlit y otras apps arrastran el entorno del momento en que se lanzaron,
    asi que un `setx` posterior no les llega y desplegarian con un token viejo.
    En Linux/CI no hay registro y se usa el entorno.
    """
    reg = {}
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as k:
            i = 0
            while True:
                try:
                    name, value, _ = winreg.EnumValue(k, i)
                except OSError:
                    break
                if name in ("CLOUDFLARE_API_TOKEN", "CLOUDFLARE_ACCOUNT_ID"):
                    reg[name] = value
                i += 1
    except (ImportError, OSError):
        pass
    token = reg.get("CLOUDFLARE_API_TOKEN") or os.environ.get("CLOUDFLARE_API_TOKEN")
    account = reg.get("CLOUDFLARE_ACCOUNT_ID") or os.environ.get("CLOUDFLARE_ACCOUNT_ID")
    return token, account


def stage_site(site_dir):
    """Copia a un directorio temporal solo los assets publicables del repo del sitio."""
    src = Path(site_dir).resolve()
    dst = Path(tempfile.mkdtemp(prefix="pages-stage-"))
    for p in sorted(src.rglob("*")):
        if not p.is_file():
            continue
        rel = p.relative_to(src)
        if any(part.startswith(".") for part in rel.parts):
            continue
        if p.suffix.lower() not in PUBLIC_SUFFIXES:
            continue
        out = dst / rel
        out.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(p, out)
    return dst


def main():
    ap = argparse.ArgumentParser(description="Deploy del sitio a Cloudflare Pages")
    ap.add_argument("--site", default=os.environ.get("BM_SITE_REPO", "../benchmarks-site"))
    ap.add_argument("--project", default="benchmarks")
    ap.add_argument("--branch", default="master",
                    help="Rama de produccion del proyecto Pages")
    ap.add_argument("--domain", default="benchmarks.sammideblas.com")
    ap.add_argument("--access-email", default=None,
                    help="Restringir Access a un email concreto (por defecto: "
                         "cualquier cuenta del IdP)")
    ap.add_argument("--github-idp", default=None,
                    help="UUID del IdP a exigir; por defecto se reutiliza el de otra app")
    ap.add_argument("--no-access", action="store_true", help="No crear la app de Access")
    ap.add_argument("--no-block-pages-dev", action="store_true",
                    help="No bloquear los subdominios *.pages.dev (por defecto SI se bloquean)")
    args = ap.parse_args()

    token, account = credentials()
    if not token or not account:
        sys.exit("Faltan CLOUDFLARE_API_TOKEN y CLOUDFLARE_ACCOUNT_ID "
                 "(entorno o HKCU\\Environment).")

    projects = api(token, f"/accounts/{account}/pages/projects")
    names = [p["name"] for p in projects.get("result", [])]
    if args.project not in names:
        created = api(token, f"/accounts/{account}/pages/projects", "POST",
                      {"name": args.project,
                       "production_branch": args.branch,
                       "compatibility": {"enable_run_worker": False}})
        print("Proyecto creado:", created["result"]["name"])

    staged = stage_site(args.site)
    print("staging:", staged, "|", len(list(staged.rglob("*"))), "entradas")
    # La API de subida directa exige multipart con manifest; wrangler lo hace bien.
    try:
        upload = subprocess.run(
            ["npx.cmd", "-y", "wrangler@4", "pages", "deploy", str(staged),
             f"--project-name={args.project}", f"--branch={args.branch}",
             "--commit-dirty=true"],
            env=dict(os.environ, WRANGLER_SEND_METRICS="false",
                     CLOUDFLARE_API_TOKEN=token,
                     CLOUDFLARE_ACCOUNT_ID=account),
            capture_output=True, text=True, shell=False)
    finally:
        shutil.rmtree(staged, ignore_errors=True)
    out = ((upload.stdout or "") + (upload.stderr or "")).strip()
    print(out[-1200:])
    if upload.returncode != 0:
        sys.exit(f"wrangler fallo (codigo {upload.returncode})")

    doms = api(token, f"/accounts/{account}/pages/projects/{args.project}/domains")
    existing = {d.get("name") or d.get("domain") for d in doms.get("result", [])}
    if args.domain not in existing:
        add = api(token, f"/accounts/{account}/pages/projects/{args.project}/domains",
                  "POST", {"name": args.domain}, strict=False)
        if add.get("success"):
            print("Dominio añadido:", add.get("result", {}).get("name"))
        else:
            print("AVISO dominio:", json.dumps(add)[:300])
        print("Comprueba el CNAME en la zona:",
              f"{args.domain} -> <subdominio>.pages.dev (proxied)")

    if not args.no_access:
        apps = api(token, f"/accounts/{account}/access/apps").get("result", [])
        app = next((a for a in apps if a.get("domain") == args.domain), None)
        idp = args.github_idp
        if not idp:
            # Reutiliza el IdP de otra app del mismo account (p. ej. sec.).
            idp = next((a["allowed_idps"][0] for a in apps if a.get("allowed_idps")), None)
        if args.access_email:
            include = [{"email": {"email": args.access_email}}]
        elif idp:
            include = [{"login_method": {"id": idp}}]
        else:
            include = [{"everyone": {}}]

        if not app:
            created = api(token, f"/accounts/{account}/access/apps", "POST",
                          {"name": args.project, "domain": args.domain,
                           "type": "self_hosted", "session_duration": "24h",
                           "policies": [{"name": "Autorizados", "decision": "allow",
                                         "include": include}]})
            if not created.get("success"):
                sys.exit("no se pudo crear la app de Access: " + json.dumps(created)[:300])
            app = created["result"]
            print("App de Access creada:", app.get("domain"))

        if idp:
            upd = api(token, f"/accounts/{account}/access/apps/{app['id']}", "PUT",
                      {"name": app.get("name") or args.project,
                       "domain": args.domain, "type": "self_hosted",
                       "session_duration": app.get("session_duration") or "24h",
                       "allowed_idps": [idp], "auto_redirect_to_identity": True})
            print("Login solo por IdP" if upd.get("success") else
                  "AVISO: no se pudo fijar el IdP: " + json.dumps(upd)[:200],
                  idp if upd.get("success") else "")
        else:
            print("AVISO: sin IdP detectado; configura login solo GitHub en el dashboard.")

        if args.access_email:
            pol = api(token, f"/accounts/{account}/access/apps/{app['id']}/policies", "POST",
                      {"name": "Cuenta autorizada", "decision": "allow",
                       "include": [{"email": {"email": args.access_email}}]})
            print("Politica restrictiva:", pol.get("result", {}).get("name"))

    if not args.no_block_pages_dev:
        sub = api(token, f"/accounts/{account}/pages/projects/{args.project}")
        subdomain = sub.get("result", {}).get("subdomain") or f"{args.project}.pages.dev"
        for host in (subdomain, f"preview-{subdomain}"):
            apps = api(token, f"/accounts/{account}/access/apps").get("result", [])
            if any(a.get("domain") == host for a in apps):
                print("Ya bloqueado:", host)
                continue
            res = api(token, f"/accounts/{account}/access/apps", "POST",
                      {"name": f"bloqueo-{host}", "domain": host,
                       "type": "self_hosted",
                       "policies": [{"name": "deny", "decision": "deny",
                                     "include": [{"everyone": {}}]}]}, strict=False)
            if res.get("success"):
                print("Bloqueado (Access deny):", host)
            elif "does not belong to zone" in json.dumps(res):
                print(f"AVISO: {host} no admite app de Access (solo existe al desplegar). "
                      "Si importa, desactiva preview deployments en Pages -> Settings.")
            else:
                print(f"AVISO: no se pudo bloquear {host}: {json.dumps(res)[:200]}")

    print("Hecho. Verifica en https://dash.cloudflare.com/")


if __name__ == "__main__":
    main()
