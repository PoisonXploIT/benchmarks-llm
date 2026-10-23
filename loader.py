import json
import re
from pathlib import Path

from paths import ROOT

# Ficheros que el parser no pudo leer: se reportan en la UI para no comparar modelos
# con huecos silenciosos.
IGNORED = []

DOMAIN_MAP = {
    "C": "Código",
    "T": "Texto",
    "R": "Razonamiento",
    "S": "Ciberseguridad",
    "L": "Lenguaje",
    "X": "Extra",
    "A": "Avanzadas",
}

ID_RE = re.compile(r"^[A-Z]\d[a-z]?$")
SCORE_RE = re.compile(
    r"^#{1,4}\s+([A-Z]\d(?:-[A-Z]\d)?)\s*[—–\-:]\s*(.+?)\s*\*{0,2}\((\d+)/(\d+)\)\*{0,2}\s*$"
)
GT_RE = re.compile(r"[Gg]ran\s+total\s*:\s*\*{0,2}(\d+)\s*/\s*(\d+)")


def _read_text(p):
    return p.read_text(encoding="utf-8-sig", errors="replace")


def _parse_filename(p):
    stem = p.stem
    m = re.search(r"(\d{4}-\d{2}-\d{2})", stem)
    fecha = m.group(1) if m else ""
    name = re.sub(r"\s+\d{4}-\d{2}-\d{2}$", "", stem)
    return name, fecha


def _domain(tid):
    m = re.match(r"^([A-Za-z])", tid)
    return DOMAIN_MAP.get(m.group(1).upper(), "") if m else ""


def _tables(txt):
    tables = []
    cur = None
    for line in txt.splitlines():
        s = line.strip()
        if s.startswith("|"):
            cells = [c.strip() for c in s.strip("|").split("|")]
            if cells and all(re.fullmatch(r":?-+:?", c) for c in cells):
                continue
            if cur is None:
                cur = {"header": cells, "rows": []}
            else:
                cur["rows"].append(cells)
        else:
            if cur is not None:
                tables.append(cur)
                cur = None
    if cur is not None:
        tables.append(cur)
    return tables


def _score_cell(s):
    s = s.strip().strip("*").strip()
    if s == "":
        return None
    m = re.fullmatch(r"(\d+)\s*/\s*(\d+)", s)
    if m:
        return (int(m.group(1)), int(m.group(2)))
    m = re.fullmatch(r"(\d+)", s)
    if m:
        return (int(m.group(1)), None)
    return None


def _num(s):
    v = _score_cell(s)
    return v[0] if v else None


def load_censura():
    out = []
    paths = list(ROOT.glob("results-*.json"))
    for d in ROOT.glob("results-censura-*"):
        paths += list(d.glob("*.json"))
    for p in sorted(paths):
        try:
            data = json.loads(_read_text(p))
        except Exception:
            IGNORED.append((str(p.relative_to(ROOT)), "JSON no parseable"))
            continue
        if "tasa_rechazo_pct" not in data:
            IGNORED.append((str(p.relative_to(ROOT)), "sin tasa_rechazo_pct"))
            continue
        data["_fuente"] = str(p.relative_to(ROOT))
        out.append(data)
    return out


def load_subagentes():
    runs = []
    for p in sorted(ROOT.glob("*/results/index.json")):
        try:
            data = json.loads(_read_text(p))
        except Exception:
            IGNORED.append((str(p.relative_to(ROOT)), "index.json no parseable"))
            continue
        bench = p.parent.parent.name
        for r in data.get("runs", []):
            r = dict(r)
            r["benchmark"] = bench
            r["_fuente"] = str(p.relative_to(ROOT))
            runs.append(r)
    return runs


def load_multiambito():
    long_rows = []
    for p in sorted(ROOT.glob("Benchmark*.md")):
        if "Ciberseguridad" in p.name or "Censura" in p.name:
            continue
        if "Subagentes Locales" in p.name:
            continue
        txt = _read_text(p)
        name, fecha = _parse_filename(p)
        model = re.sub(r"^Benchmark\s+", "", name)
        fuente = str(p.relative_to(ROOT))
        before = len(long_rows)

        inline = {}
        for line in txt.splitlines():
            m = SCORE_RE.match(line.strip())
            if m:
                tid, title, n, d = m.groups()
                inline[tid] = (title.strip(), int(n), int(d))
        if inline:
            for tid, (title, n, d) in inline.items():
                long_rows.append({
                    "model": model, "test": tid, "title": title,
                    "domain": _domain(tid), "score": n, "max": d,
                    "fuente": fuente, "fecha": fecha, "source_tag": "individual",
                })
            continue

        comp, single = _score_tables(txt)
        tag = "subagentes" if "Subagentes" in p.name else "directo"
        for t, score_cols in comp:
            _expand_comparative(t, score_cols, fuente, fecha, tag, long_rows)
        if not comp:
            for t, score_cols in single:
                _expand_single(t, score_cols[0], model, fuente, fecha, long_rows)
        if len(long_rows) == before:
            IGNORED.append((fuente, "sin puntuaciones reconocibles"))
    return long_rows


def _score_tables(txt):
    comp = []
    single = []
    for t in _tables(txt):
        hdr = t["header"]
        rows = t["rows"]
        if len(hdr) < 2 or not rows:
            continue
        first = hdr[0].strip().lstrip("#").strip().lower()
        if first not in ("", "prueba", "test", "id", "n", "nº", "no"):
            continue
        if not any(ID_RE.match(r[0].strip()) for r in rows if r and r[0].strip()):
            continue
        score_cols = []
        for ci in range(1, len(hdr)):
            vals = [r[ci] for r in rows if ci < len(r) and r[ci].strip() != ""]
            if vals and all(_score_cell(v) is not None for v in vals):
                score_cols.append(ci)
        if len(score_cols) >= 2:
            comp.append((t, score_cols))
        elif len(score_cols) == 1 and hdr[score_cols[0]].strip().lower() in ("pts", "puntos", "nota", "score", "puntuacion"):
            single.append((t, score_cols))
    return comp, single


def _expand_comparative(t, score_cols, fuente, fecha, tag, long_rows):
    hdr = t["header"]
    models = [hdr[ci].strip() for ci in score_cols]
    for r in t["rows"]:
        if not r:
            continue
        idcell = r[0].strip().strip("*").strip()
        if idcell.lower() in ("total", "suma", ""):
            continue
        if not ID_RE.match(idcell):
            continue
        title = r[1].strip() if len(r) > 1 else ""
        for i, ci in enumerate(score_cols):
            v = _score_cell(r[ci]) if ci < len(r) else None
            if v is None:
                continue
            long_rows.append({
                "model": models[i], "test": idcell, "title": title,
                "domain": _domain(idcell), "score": v[0],
                "max": v[1] if v[1] is not None else 5,
                "fuente": fuente, "fecha": fecha, "source_tag": tag,
            })


def _expand_single(t, score_col, model, fuente, fecha, long_rows):
    for r in t["rows"]:
        if not r:
            continue
        idcell = r[0].strip().strip("*").strip()
        if idcell.lower() in ("total", "suma", ""):
            continue
        if not ID_RE.match(idcell):
            continue
        title = r[1].strip() if len(r) > 1 else ""
        v = _score_cell(r[score_col]) if score_col < len(r) else None
        if v is None:
            continue
        long_rows.append({
            "model": model, "test": idcell, "title": title,
            "domain": _domain(idcell), "score": v[0],
            "max": v[1] if v[1] is not None else 5,
            "fuente": fuente, "fecha": fecha, "source_tag": "individual",
        })


SCRIPT_LANG = {".ps1": "powershell", ".py": "python", ".js": "javascript"}


def _script_desc(p, txt):
    if p.suffix == ".py":
        m = re.search(r'"""\n(.*)', txt)
        return m.group(1).strip() if m else ""
    for line in txt.splitlines()[:20]:
        s = line.strip()
        if p.suffix == ".ps1" and s.startswith("#") and not s.startswith("#!"):
            return s.lstrip("#").strip()
        if p.suffix == ".js" and s.startswith("*"):
            return s.lstrip("*").strip()
    return ""


def load_scripts():
    out = []

    def add(p, family):
        txt = _read_text(p)
        head = "\n".join(txt.splitlines()[:20])
        api = re.search(r'^\$API\s*=\s*"([^"]+)"', head, re.MULTILINE)
        model = re.search(r'^\$MODEL\s*=\s*"([^"]+)"', head, re.MULTILINE)
        out.append({
            "family": family, "name": p.name,
            "path": str(p.relative_to(ROOT)),
            "language": SCRIPT_LANG.get(p.suffix.lower(), "text"),
            "lines": len(txt.splitlines()), "bytes": p.stat().st_size,
            "api": api.group(1) if api else "",
            "model": model.group(1) if model else "",
            "desc": _script_desc(p, txt), "content": txt,
        })

    for p in sorted(ROOT.glob("run-*.ps1")):
        fam = "Ciberseguridad" if "cybersec" in p.name.lower() else "Multi-ámbito"
        add(p, fam)
    for p in sorted(ROOT.glob("bench_*.py")):
        add(p, "Censura")
    for d in sorted(ROOT.iterdir()):
        js = d / "run-benchmark.js"
        if d.is_dir() and js.exists():
            add(js, d.name)
    return out


def load_cibersec():
    out = []
    for p in sorted(ROOT.glob("Benchmark Ciberseguridad*.md")):
        txt = _read_text(p)
        name, fecha = _parse_filename(p)
        for t in _tables(txt):
            hdr = t["header"]
            if not hdr or not hdr[0].lower().strip().strip("`").startswith(("prompt", "id")):
                continue
            comparative = "Base" in hdr and "Heretic" in hdr
            rows = []
            for row in t["rows"]:
                cells = row
                if not cells:
                    continue
                pid = cells[0].strip().strip("`")
                if not re.match(r"^[a-z]\d+_[a-z0-9_]+$", pid):
                    continue
                cat = cells[1].strip() if len(cells) > 1 else ""
                if comparative and len(cells) >= 4:
                    rows.append({
                        "prompt": pid, "categoria": cat,
                        "base": _num(cells[2]), "heretic": _num(cells[3]),
                    })
                elif not comparative and len(cells) >= 3:
                    rows.append({
                        "prompt": pid, "categoria": cat,
                        "nota": _num(cells[2]),
                        "refusal": cells[3].strip() if len(cells) > 3 else "",
                    })
            if not rows:
                IGNORED.append((str(p.relative_to(ROOT)), "tabla de ciberseguridad vacia"))
            if rows:
                out.append({
                    "_fuente": str(p.relative_to(ROOT)),
                    "model": name, "fecha": fecha,
                    "comparative": comparative, "rows": rows,
                })
                break
    return out
