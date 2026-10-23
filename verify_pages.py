"""Verifica los assets de un sitio en Cloudflare Pages detras de Access.

Crea una politica Access `bypass` temporal, comprueba los ficheros, la borra
siempre (try/finally) y sale con codigo 1 si algo falla.

Token necesario: Access: Apps and Policies Edit.
Uso:
    python verify_pages.py --domain benchmarks.sammideblas.com \
        --expect /data.json=json --expect /app.js=js
"""
import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request

API = "https://api.cloudflare.com/client/v4"
UA = "python-urllib/3.11"  # el UA por defecto de urllib lo bloquea el WAF


def api(token, path, method="GET", payload=None):
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(API + path, data=data, method=method)
    req.add_header("Authorization", "Bearer " + token)
    if data:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=45) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        return {"__status": e.code, "body": e.read().decode(errors="replace")[:300]}


def fetch(url, headers=None):
    req = urllib.request.Request(url)
    req.add_header("User-Agent", UA)
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, (r.headers.get("content-type") or ""), r.read()
    except urllib.error.HTTPError as e:
        return e.code, (e.headers.get("content-type") or ""), e.read()[:400]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--domain", required=True)
    ap.add_argument("--expect", action="append", default=[],
                    help="/ruta=tipo, donde tipo es json|js|html|css|404")
    ap.add_argument("--wait", type=int, default=20, help="segundos maximos de espera")
    args = ap.parse_args()

    token = os.environ.get("CLOUDFLARE_API_TOKEN")
    account = os.environ.get("CLOUDFLARE_ACCOUNT_ID")
    if not token or not account:
        sys.exit("faltan CLOUDFLARE_API_TOKEN y CLOUDFLARE_ACCOUNT_ID")

    apps = api(token, f"/accounts/{account}/access/apps").get("result", [])
    app = next((a for a in apps if a.get("domain") == args.domain), None)
    if not app:
        sys.exit(f"no hay app de Access para {args.domain}")

    pol = api(token, f"/accounts/{account}/access/apps/{app['id']}/policies", "POST",
              {"name": "verificacion-temporal", "decision": "bypass",
               "include": [{"everyone": {}}]})
    pid = pol.get("result", {}).get("id")
    if not pid:
        sys.exit("no se pudo crear la politica temporal: " + json.dumps(pol)[:300])

    fallos = []
    try:
        paths = [e.split("=", 1)[0] for e in args.expect]
        deadline = time.time() + args.wait
        while time.time() < deadline:
            status, ctype, body = fetch(f"https://{args.domain}{paths[0]}")
            if status == 200 and "html" not in ctype:
                break
            time.sleep(3)
        for item in args.expect:
            path, kind = item.split("=", 1)
            status, ctype, body = fetch(f"https://{args.domain}{path}")
            ok = True
            extra = ""
            if kind == "404":
                ok = status == 404
            elif status != 200:
                ok = False
            elif kind == "json":
                try:
                    data = json.loads(body)
                    extra = f"{len(data)} claves"
                except Exception as e:
                    ok, extra = False, f"json invalido: {e}"
            elif kind == "js":
                ok = "javascript" in ctype
            elif kind == "html":
                ok = "html" in ctype
            elif kind == "css":
                ok = "css" in ctype
            print(f"{'OK ' if ok else 'FALLO'} {path} -> {status} {ctype} {len(body)}B {extra}")
            if not ok:
                fallos.append(path)
    finally:
        res = api(token, f"/accounts/{account}/access/apps/{app['id']}/policies/{pid}",
                  "DELETE")
        print("politica temporal:", "borrada" if res.get("success") else "NO BORRADA")
        left = api(token, f"/accounts/{account}/access/apps/{app['id']}/policies").get("result", [])
        print("politicas vigentes:", [(p["name"], p["decision"]) for p in left])
        if not res.get("success"):
            sys.exit(2)

    if fallos:
        sys.exit(1)


if __name__ == "__main__":
    main()
