"""Sube el sitio de datos a Cloudflare Pages por API.

Requiere en el entorno:
    CLOUDFLARE_API_TOKEN   (scopes: Pages Edit, Zone Read, Access Read-Write)
    CLOUDFLARE_ACCOUNT_ID

Uso:
    python deploy_pages.py --site C:\\Users\\Sammi\\AI\\benchmarks-site \
        --project benchmarks --domain benchmarks.sammideblas.com \
        --access-email tu@email.com

Idempotente: si el proyecto existe, solo redeploya.
"""
import argparse
import io
import json
import os
import sys
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

API = "https://api.cloudflare.com/client/v4"


def api(token, path, method="GET", payload=None, raw=None, content_type="application/json"):
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
        raise SystemExit(f"Cloudflare {e.code} en {path}: {body[:400]}")


def zip_site(site_dir):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for p in sorted(Path(site_dir).rglob("*")):
            if p.is_file() and ".git" not in p.parts:
                z.write(p, p.relative_to(site_dir).as_posix())
    return buf.getvalue()


def main():
    ap = argparse.ArgumentParser(description="Deploy del sitio a Cloudflare Pages")
    ap.add_argument("--site", default="C:\\Users\\Sammi\\AI\\benchmarks-site")
    ap.add_argument("--project", default="benchmarks")
    ap.add_argument("--domain", default="benchmarks.sammideblas.com")
    ap.add_argument("--access-email", default=None,
                    help="Restringir Access a un email concreto (por defecto: "
                         "cualquier cuenta de GitHub)")
    ap.add_argument("--no-access", action="store_true", help="No crear la app de Access")
    ap.add_argument("--no-block-pages-dev", action="store_true",
                    help="No bloquear los subdominios *.pages.dev (por defecto SI se bloquean)")
    args = ap.parse_args()

    token = os.environ.get("CLOUDFLARE_API_TOKEN")
    account = os.environ.get("CLOUDFLARE_ACCOUNT_ID")
    if not token or not account:
        sys.exit("Faltan CLOUDFLARE_API_TOKEN y CLOUDFLARE_ACCOUNT_ID en el entorno.")

    projects = api(token, f"/accounts/{account}/pages/projects")
    names = [p["name"] for p in projects.get("result", [])]
    if args.project not in names:
        created = api(token, f"/accounts/{account}/pages/projects", "POST",
                      {"name": args.project,
                       "compatibility": {"enable_run_worker": False}})
        print("Proyecto creado:", created["result"]["name"])

    blob = zip_site(args.site)
    dep = api(token, f"/accounts/{account}/pages/projects/{args.project}/deployments",
              "POST", raw=blob, content_type="application/zip")
    r = dep.get("result", {})
    print("Deploy:", r.get("url"), "| status:", r.get("status"))

    doms = api(token, f"/accounts/{account}/pages/projects/{args.project}/domains")
    existing = {d["domain"] for d in doms.get("result", [])}
    if args.domain not in existing:
        add = api(token, f"/accounts/{account}/pages/projects/{args.project}/domains",
                  "POST", {"domain": args.domain})
        print("Dominio añadido:", add.get("result", {}).get("domain"))

    if not args.no_access:
        apps = api(token, f"/accounts/{account}/access/apps")
        app = next((a for a in apps.get("result", []) if a.get("domain") == args.domain), None)
        if not app:
            app = api(token, f"/accounts/{account}/access/apps", "POST",
                      {"name": args.project, "domain": args.domain,
                       "type": "self_hosted", "policy": "allow"})
            print("App de Access creada:", app["result"]["domain"])
        # Login solo con GitHub: sin magic link por email.
        try:
            api(token, f"/accounts/{account}/access/apps/{app['id']}", "PATCH",
                {"allowed_login_methods": ["github"]})
            print("Login restringido a GitHub.")
        except SystemExit:
            print("AVISO: el token no permite restringir el metodo de login por API. "
                  "Hazlo en Zero Trust -> Access -> Apps -> <project> -> Settings -> "
                  "Login methods: solo GitHub.")
        if args.access_email:
            pol = api(token, f"/accounts/{account}/access/apps/{app['id']}/policies", "POST",
                      {"name": "Cuenta autorizada", "action": "allow", "scope": "bypass",
                       "rules": [{"require": {"email": args.access_email}}]})
            print("Politica restrictiva:", pol.get("result", {}).get("name"))
        else:
            pol = api(token, f"/accounts/{account}/access/apps/{app['id']}/policies", "POST",
                      {"name": "Cualquier cuenta de GitHub", "action": "allow",
                       "scope": "bypass", "rules": []})
            print("Politica: cualquier usuario autenticado con GitHub.")

    if not args.no_block_pages_dev:
        for host in (f"{args.project}.pages.dev", f"preview-{args.project}.pages.dev"):
            apps = api(token, f"/accounts/{account}/access/apps")
            if any(a.get("domain") == host for a in apps.get("result", [])):
                print("Ya bloqueado:", host)
                continue
            try:
                api(token, f"/accounts/{account}/access/apps", "POST",
                    {"name": f"bloqueo-{host}", "domain": host, "type": "self_hosted",
                     "policy": "deny"})
                print("Bloqueado (Access deny):", host)
            except SystemExit as e:
                print(f"AVISO: no se pudo bloquear {host}: {e}")
                print("Alternativa manual: Zero Trust -> Access -> Apps -> nueva app "
                      f"para {host} con politica Deny.")

    print("Hecho. Verifica en https://dash.cloudflare.com/")


if __name__ == "__main__":
    main()
