import gzip
import json
import os
import sys
import time
from datetime import timedelta
from pathlib import Path

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent))
import loader
import sanitizer
try:
    import orchestrator  # no existe en el contenedor de deploy (ver .dockerignore)
except ImportError:  # pragma: no cover
    orchestrator = None
try:
    import publish  # publicacion a Pages: solo tiene sentido en local
except ImportError:  # pragma: no cover
    publish = None

st.set_page_config(
    page_title="Benchmarks LLM",
    layout="wide",
    page_icon="static/favicon.svg",
)

DOMAIN_ORDER = ["Código", "Texto", "Razonamiento", "Ciberseguridad", "Lenguaje", "Extra", "Avanzadas"]


def _tail_file(path, n=60):
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
        return "".join(lines[-n:]).rstrip("\n")
    except OSError:
        return ""


_SNAP_DIR = Path(__file__).resolve().parent / "data_snapshot"
SNAPSHOT = _SNAP_DIR / "snapshot.json.gz"
SNAPSHOT_PUBLIC = _SNAP_DIR / "snapshot_public.json.gz"
# En el deploy (Railway/Cloudflare) no hay vault: se usa el snapshot. BM_READONLY
# ademas desactiva el orquestador (no hay llama-server ni GPU en el servidor) y
# obliga al snapshot publico (sanitizado: sin respuestas crudas ni nombres reales).
READ_ONLY = bool(os.environ.get("BM_READONLY"))
ACTIVE_SNAPSHOT = SNAPSHOT_PUBLIC if (READ_ONLY and SNAPSHOT_PUBLIC.exists()) else SNAPSHOT
USE_SNAPSHOT = READ_ONLY or (not loader.ROOT.exists() and ACTIVE_SNAPSHOT.exists())


@st.cache_data(show_spinner=False)
def get_data():
    if USE_SNAPSHOT:
        with gzip.open(ACTIVE_SNAPSHOT, "rt", encoding="utf-8") as f:
            d = json.load(f)
        return (d.get("censura", []), d.get("subagentes", []), d.get("multi", []),
                d.get("cibersec", []), d.get("scripts", []), d.get("ignored", []))
    loader.IGNORED.clear()
    censura = loader.load_censura()
    subagentes = loader.load_subagentes()
    multi = loader.load_multiambito()
    cibersec = loader.load_cibersec()
    scripts = loader.load_scripts()
    return censura, subagentes, multi, cibersec, scripts, list(loader.IGNORED)


def run_label(r):
    tag = r.get("source_tag", "individual")
    return r["model"] if tag == "individual" else f"{r['model']} ({tag})"


def multi_df(rows):
    if not rows:
        return pd.DataFrame(columns=["model", "test", "title", "domain", "score", "max",
                                     "fuente", "fecha", "source_tag", "run", "pct"])
    df = pd.DataFrame(rows)
    df["run"] = df.apply(run_label, axis=1)
    df["pct"] = df["score"] / df["max"].replace(0, 1) * 100
    df["es_acierto"] = df["score"] <= df["max"]
    return df


def short_name(fuente):
    name = Path(fuente).stem
    return name[len("Benchmark "):] if name.startswith("Benchmark ") else name


if USE_SNAPSHOT:
    st.caption(
        f"Modo snapshot: `{SNAPSHOT.name}`. Los datos no se leen del vault "
        "(actualiza el snapshot con `python export_snapshot.py`)."
    )
    if not SNAPSHOT.exists():
        st.error(
            f"No hay snapshot en `{SNAPSHOT}`. Generalo en local con "
            "`python export_snapshot.py` antes de desplegar."
        )
        st.stop()
elif not loader.ROOT.exists():
    st.error(
        f"La raiz de datos no existe: `{loader.ROOT}`. Crea la carpeta o define "
        "OQ_DATA_ROOT (o `data_root` en config.json) antes de usar el dashboard."
    )
    st.stop()

censura, subagentes, multi, cibersec, scripts, ignored = get_data()
mdf = multi_df(multi)
if "data_loaded_at" not in st.session_state:
    st.session_state.data_loaded_at = time.strftime("%Y-%m-%d %H:%M:%S")


def page_resumen():
    st.title("Resumen")
    st.caption("Panorama de todos los benchmarks locales: multi-ámbito, censura, ciberseguridad y subagentes.")
    st.caption(f"Raiz de datos: `{loader.ROOT}` - cargada el {st.session_state.data_loaded_at}")
    if ignored:
        st.warning(
            f"{len(ignored)} ficheros no se pudieron parsear (no entran en las metricas): "
            + "; ".join(f"{p} ({r})" for p, r in ignored[:12])
        )

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Modelos", mdf["run"].nunique() if not mdf.empty else 0,
              help="Modelos distintos evaluados en multi-ámbito")
    c2.metric("Pruebas", len(mdf), help="Filas prueba-modelo en multi-ámbito")
    c3.metric("Servidores censura", len(censura), help="Informes de censura cargados")
    c4.metric("Runs subagentes", len(subagentes), help="Ejecuciones de subagentes registradas")

    st.subheader("Inventario de fuentes")
    if not mdf.empty:
        inv = (mdf.groupby("fuente", sort=True)
                  .agg(modelos=("run", "nunique"), pruebas=("run", "size"),
                       tipo=("source_tag", lambda s: ", ".join(sorted(set(s)))))
                  .reset_index())
        inv["fuente"] = inv["fuente"].map(short_name)
        inv = inv.rename(columns={"fuente": "Fichero", "modelos": "Modelos",
                                  "pruebas": "Pruebas", "tipo": "Tipo"})
        inv = inv.sort_values("Modelos", ascending=False)
        st.dataframe(inv, hide_index=True, width="stretch")
    else:
        st.info("Sin datos multi-ámbito.")


def page_censura():
    st.title("Censura")
    if not censura:
        st.info("Sin datos de censura.")
        return

    cdf = pd.DataFrame([{
        "server": c.get("server", c.get("_fuente", "")),
        "total": c.get("total", 0),
        "rechazos_R": c.get("rechazos_R", 0),
        "cumplimiento_C": c.get("cumplimiento_C", 0),
        "evasivo_E": c.get("evasivo_E", 0),
        "errores": c.get("errores", 0),
        "tasa_rechazo_pct": c.get("tasa_rechazo_pct", 0),
        "tasa_compliance_pct": c.get("tasa_compliance_pct", 0),
        "fuente": c.get("_fuente", ""),
    } for c in censura])

    st.subheader("Rechazo vs cumplimiento por servidor")
    st.dataframe(cdf.sort_values("tasa_rechazo_pct", ascending=False),
                 hide_index=True, width="stretch")

    m = cdf.melt(id_vars="server", value_vars=["rechazos_R", "cumplimiento_C", "evasivo_E", "errores"],
                 var_name="Tipo", value_name="N")
    fig = px.bar(m, x="server", y="N", color="Tipo", barmode="group",
                 title="Desglose de respuestas por servidor")
    fig.update_xaxes(tickangle=45)
    st.plotly_chart(fig, width="stretch")
    st.caption("Lectura alternativa: barras agrupadas por servidor con el numero de rechazos (R), "
               "cumplimientos (C), respuestas evasivas (E) y errores.")

    st.subheader("Tasa de rechazo por categoría")
    cat_rows = []
    for c in censura:
        for cat, d in (c.get("por_categoria") or {}).items():
            n = d.get("n", 0)
            cat_rows.append({
                "server": c.get("server", "?"), "categoria": cat,
                "rechazos": d.get("R", 0), "cumplimiento": d.get("C", 0),
                "evasivo": d.get("E", 0), "total": n,
            })
    if cat_rows:
        catdf = pd.DataFrame(cat_rows)
        catm = catdf.melt(id_vars=["server", "categoria"],
                          value_vars=["rechazos", "cumplimiento", "evasivo"],
                          var_name="Tipo", value_name="N")
        fig2 = px.bar(catm, x="categoria", y="N", color="Tipo", barmode="stack",
                      facet_col="server", title="Rechazos / cumplimiento por categoría y servidor")
        fig2.update_xaxes(tickangle=45)
        fig2.for_each_annotation(lambda a: a.update(text=a.text.split("=")[-1]))
        st.plotly_chart(fig2, width="stretch")
        st.caption("Lectura alternativa: por cada servidor y categoria, cuantos rechazos, "
                   "cumplimientos y evasivos hay. La tabla de arriba tiene los mismos numeros.")

        st.subheader("Heatmap de rechazo por categoría")
        piv = catdf.pivot_table(index="server", columns="categoria", values="rechazos",
                                aggfunc="sum", fill_value=0)
        fig3 = px.imshow(piv, text_auto=True, aspect="auto", color_continuous_scale="Reds",
                         title="Nº de rechazos (R) por servidor y categoría")
        st.plotly_chart(fig3, width="stretch")
        st.caption("Lectura alternativa: filas = servidores, columnas = categorias; el numero de "
                   "cada celda es el total de rechazos de ese servidor en esa categoria.")



def page_multi():
    st.title("Multi-ámbito")
    if mdf.empty:
        st.info("Sin datos multi-ámbito.")
        return

    # Filtros inline, encima del ranking: se ven sin abrir la sidebar.
    f_tag, f_runs, f_min = st.columns([2, 4, 1])
    with f_tag:
        tags = ["todas"] + sorted(mdf["source_tag"].unique().tolist())
        tag_sel = st.selectbox("Fuente", tags, key="oq_tag")
    with f_runs:
        runs_all = sorted(mdf["run"].unique())
        sel_runs = st.multiselect("Modelos", runs_all, default=runs_all, key="oq_runs")
    with f_min:
        min_pruebas = st.number_input("Pruebas mínimas", 0, 100, value=0, key="oq_minpruebas",
                                      help="Descarta modelos con pocas pruebas: comparar recuentos "
                                         "distintos es injusto")

    view = mdf.copy() if tag_sel == "todas" else mdf[mdf["source_tag"] == tag_sel].copy()
    if sel_runs:
        view = view[view["run"].isin(sel_runs)]
    counts = view.groupby("run").size()
    if min_pruebas:
        view = view[view["run"].isin(counts[counts >= min_pruebas].index)]
    if view.empty:
        st.warning("Ningún modelo cumple los filtros.")
        return

    # Un acierto no puede superar su maximo. Las filas con score > max son medidas
    # de otra cosa (p. ej. 'Len (tok)': tokens generados) y contaminan el ranking.
    anom = view[~view["es_acierto"]]
    if not anom.empty:
        st.warning(
            f"{len(anom)} filas con score > maximo: no son aciertos (son medidas de "
            f"tokens u otra magnitud, p. ej. 'Len (tok)'). Se excluyen del ranking, "
            "el heatmap y la comparativa. Modelos afectados: "
            f"{', '.join(sorted(anom['run'].unique())[:6])}."
        )
        view = view[view["es_acierto"]]
        if view.empty:
            return

    st.caption(f"{view['run'].nunique()} modelos, {len(view)} filas prueba-modelo, "
               f"{view['test'].nunique()} pruebas distintas.")

    order = [d for d in DOMAIN_ORDER] + sorted(
        [d for d in view["domain"].unique() if d not in DOMAIN_ORDER]
    )
    rank = view.groupby("run").agg(score=("score", "sum"), max=("max", "sum"),
                                   pruebas=("test", "size")).reset_index()
    rank["pct"] = rank["score"] / rank["max"].replace(0, 1) * 100
    rank = rank.sort_values("pct", ascending=False)

    with st.container(border=True):
        st.subheader("Vista global")
        vista = st.radio(
            "Granularidad",
            ["Modelo x dominio", "Modelo x prueba (matriz completa)"],
            key="oq_vista", horizontal=True,
        )
        if vista == "Modelo x dominio":
            dm = view.groupby(["run", "domain"]).agg(score=("score", "sum"),
                                                     max=("max", "sum")).reset_index()
            dm["pct"] = dm["score"] / dm["max"].replace(0, 1) * 100
            piv = dm.pivot_table(index="run", columns="domain", values="pct", aggfunc="mean")
            piv = piv[[c for c in order if c in piv.columns]]
            fig = px.imshow(piv, text_auto=".0f", aspect="auto",
                            color_continuous_scale="RdYlGn", zmin=0, zmax=100)
            fig.update_layout(height=max(300, 30 * len(piv) + 120))
            st.plotly_chart(fig, width="stretch")
            st.caption("Una celda por modelo y dominio: media del % de acierto de todas sus "
                       "pruebas de ese dominio. Es la vista rapida para ver donde gana cada uno. "
                       "Celda vacia = el modelo no tiene pruebas de ese dominio.")
        else:
            test_order = [t for d in order for t in sorted(view[view["domain"] == d]["test"].unique())]
            piv = view.pivot_table(index="run", columns="test", values="pct", aggfunc="mean")
            piv = piv[[t for t in test_order if t in piv.columns]]
            fig = px.imshow(piv, text_auto=".0f", aspect="auto",
                            color_continuous_scale="RdYlGn", zmin=0, zmax=100)
            fig.update_layout(height=max(320, 26 * len(piv) + 140))
            st.plotly_chart(fig, width="stretch")
            st.caption(f"Matriz completa: {len(piv)} modelos x {len(piv.columns)} pruebas "
                       f"({len(piv) * len(piv.columns)} celdas), columnas agrupadas por dominio. "
                       "Si se lee mal, sube 'Pruebas minimas' o usa la vista por dominio.")

    with st.container(border=True):
        st.subheader("Comparativa lado a lado")
        cmp_models = st.multiselect("Modelos a comparar", list(rank["run"]),
                                    default=list(rank.head(4)["run"]), key="oq_cmp",
                                    help="Dos o mas modelos; la tabla y el radar usan los mismos filtros")
        if len(cmp_models) < 2:
            st.info("Selecciona al menos 2 modelos para comparar.")
        else:
            sub = view[view["run"].isin(cmp_models)]
            dm = sub.groupby(["run", "domain"]).agg(score=("score", "sum"),
                                                    max=("max", "sum")).reset_index()
            dm["pct"] = dm["score"] / dm["max"].replace(0, 1) * 100
            doms = [d for d in order if d in dm["domain"].unique()]
            radar = go.Figure()
            for m in cmp_models:
                vals = [float(dm[(dm["run"] == m) & (dm["domain"] == d)]["pct"].mean())
                        if not dm[(dm["run"] == m) & (dm["domain"] == d)].empty else None
                        for d in doms]
                radar.add_trace(go.Scatterpolar(r=vals + vals[:1], theta=doms + doms[:1],
                                               name=m, connectgaps=False))
            radar.update_layout(polar=dict(radialaxis=dict(visible=True, range=[0, 100])),
                                height=420, showlegend=True)
            st.plotly_chart(radar, width="stretch")
            st.caption("Radar = % de acierto por dominio. Un modelo sin pruebas en un dominio "
                       "no cierra el poligono en ese eje: no es un cero.")
            wide = sub.pivot_table(index="test", columns="run", values="pct", aggfunc="mean")
            wide = wide[[m for m in cmp_models if m in wide.columns]]
            wide["diferencia"] = wide.max(axis=1) - wide.min(axis=1)
            st.dataframe(wide.sort_values("diferencia", ascending=False).round(1),
                         hide_index=True, width="stretch")
            st.caption("Filas = pruebas, columnas = modelos. 'diferencia' = distancia entre el "
                       "mejor y el peor en esa prueba: ordenado de mayor a menor discriminacion. "
                       "Celda vacia = esa prueba no se corrio con ese modelo.")

    with st.container(border=True):
        st.subheader("Ranking por puntuacion total")
        fig = px.bar(rank, x="run", y="pct", text=rank["pct"].round(1).astype(str) + "%",
                     color="pct", color_continuous_scale="Viridis")
        fig.update_yaxes(title="% acierto", range=[0, 100])
        fig.update_xaxes(tickangle=45)
        fig.update_layout(coloraxis_showscale=False, height=380)
        st.plotly_chart(fig, width="stretch")
        st.caption("Barra = % de acierto sobre el maximo de las pruebas de ese modelo. El recuento "
                   "de pruebas cambia por modelo: sube 'Pruebas minimas' para comparar solo modelos "
                   "con volumen comparable.")
        st.dataframe(rank[["run", "pruebas", "score", "max", "pct"]].round(1),
                     hide_index=True, width="stretch")

    with st.container(border=True):
        st.subheader("Heatmap por dominio")
        for dom in order:
            sub = view[view["domain"] == dom]
            if sub.empty:
                continue
            piv = sub.pivot_table(index="run", columns="test", values="pct", aggfunc="mean")
            fig = px.imshow(piv, text_auto=".0f", aspect="auto",
                            color_continuous_scale="RdYlGn", zmin=0, zmax=100)
            fig.update_layout(height=max(240, 26 * len(piv) + 120), title=f"{dom}")
            st.plotly_chart(fig, width="stretch")
            st.caption(f"Filas = modelos, columnas = pruebas de {dom} "
                       f"({len(piv)} modelos x {len(piv.columns)} pruebas). "
                       "Celda vacia = esa prueba no se hizo con ese modelo.")

    with st.container(border=True):
        st.subheader("Desglose por dominio")
        dom = view.groupby(["run", "domain"]).agg(score=("score", "sum"), max=("max", "sum")).reset_index()
        dom["pct"] = dom["score"] / dom["max"].replace(0, 1) * 100
        fig3 = px.bar(dom, x="run", y="pct", color="domain", barmode="group",
                      category_orders={"domain": order})
        fig3.update_yaxes(range=[0, 100], title="% acierto")
        fig3.update_xaxes(tickangle=45)
        fig3.update_layout(height=380)
        st.plotly_chart(fig3, width="stretch")
        st.caption("Compara dominios dentro del mismo modelo; no cruza modelos con recuentos distintos.")
def page_cibersec():
    st.title("Ciberseguridad")
    if not cibersec:
        st.info("Sin datos de ciberseguridad.")
        return
    for rep in cibersec:
        rdf = pd.DataFrame(rep["rows"])
        st.subheader(f"{rep['model']} ({rep['fecha']}) - "
                     f"{'comparativa' if rep['comparative'] else 'individual'}")
        if rep["comparative"]:
            rdf = rdf[["prompt", "categoria", "base", "heretic"]]
            st.dataframe(rdf, hide_index=True, width="stretch")
            g = rdf.groupby("categoria").agg(base=("base", "sum"),
                                             heretic=("heretic", "sum")).reset_index()
            gm = g.melt(id_vars="categoria", value_vars=["base", "heretic"],
                        var_name="Modelo", value_name="Pts")
            fig = px.bar(gm, x="categoria", y="Pts", color="Modelo", barmode="group",
                         title="Puntos por categoría")
            st.plotly_chart(fig, width="stretch")
        else:
            rdf = rdf[["prompt", "categoria", "nota", "refusal"]]
            st.dataframe(rdf, hide_index=True, width="stretch")
            st.caption("Refusal: C = cumple, R = rechaza, E = evasivo")


def page_subag():
    st.title("Subagentes")
    if not subagentes:
        st.info("Sin datos de subagentes.")
        return

    sdf = pd.DataFrame(subagentes)
    c1, c2, c3 = st.columns(3)
    c1.metric("Runs", len(sdf))
    c2.metric("Errores", int((sdf["exitCode"] != 0).sum()) if "exitCode" in sdf else 0)
    c3.metric("Modelos", sdf["model"].nunique() if "model" in sdf else 0)

    st.subheader("Resultados por modelo")
    if {"model", "exitCode"}.issubset(sdf.columns):
        agg = sdf.groupby("model").agg(
            runs=("exitCode", "size"),
            exit0=("exitCode", lambda x: (x == 0).sum()),
            media_seg=("durationSec", "mean"),
        ).reset_index()
        agg["ok_pct"] = agg["exit0"] / agg["runs"] * 100
        agg = agg.sort_values("ok_pct", ascending=False)
        st.dataframe(agg, hide_index=True, width="stretch")

    if "stopReason" in sdf.columns:
        sr = sdf["stopReason"].value_counts().reset_index()
        sr.columns = ["stopReason", "N"]
        fig = px.bar(sr, x="stopReason", y="N", title="Distribución de stopReason")
        st.plotly_chart(fig, width="stretch")
        st.caption("Lectura alternativa: tabla de recuentos por motivo de parada "
                  "(" + ", ".join(f"{r.stopReason}={r.N}" for r in sr.itertuples()) + ").")

    st.subheader("Detalle de runs")
    cols = [c for c in ["benchmark", "agent", "model", "test", "domain",
                        "durationSec", "exitCode", "stopReason", "thinkingLength"] if c in sdf.columns]
    st.dataframe(sdf[cols], hide_index=True, width="stretch")


def page_scripts():
    st.title("Scripts de benchmark")
    if not scripts:
        st.info("Sin scripts encontrados.")
        return

    df = pd.DataFrame([{
        "Familia": s["family"], "Script": s["name"], "Lengua": s["language"],
        "Lineas": s["lines"], "KB": round(s["bytes"] / 1024, 1),
        "Modelo": s["model"],
    } for s in scripts])
    st.dataframe(df, hide_index=True, width="stretch")

    labels = [f"{s['family']} :: {s['name']}" for s in scripts]
    sel = st.selectbox("Script", labels)
    s = next(x for x in scripts if f"{x['family']} :: {x['name']}" == sel)

    c1, c2 = st.columns(2)
    c1.metric("Lineas", s["lines"])
    c2.metric("Tamaño (KB)", round(s["bytes"] / 1024, 1))

    bits = [f"Ruta: `{s['path']}`"]
    if s["desc"]:
        bits.append(f"Nota: {s['desc']}")
    if s["api"]:
        bits.append(f"API: `{s['api']}`")
    if s["model"]:
        bits.append(f"Modelo: `{s['model']}`")
    st.caption("  \n".join(bits))

    st.code(s["content"], language=s["language"], height=500)
    st.download_button(f"Descargar {s['name']}", data=s["content"],
                       file_name=s["name"], mime="text/plain")


def page_guia():
    st.title("Guia")
    st.markdown("""
Este dashboard lee los informes de benchmark de la **raiz de datos** (por defecto la carpeta
padre de `app/`, o la ruta del env `OQ_DATA_ROOT`) y los convierte en graficas. La pagina
**Orquestar** ademas ejecuta benchmarks completos sin salir de la app.

## Orquestador: como funciona
1. Arranca `llama-server` (llama.cpp, solo en 127.0.0.1) con el GGUF que elijas.
2. Lanza la bateria elegida contra el modelo local.
3. Un **modelo nube** via endpoint OpenAI-compatible juzga cada respuesta (JSON estricto).
4. Escribe el informe **Markdown** en la raiz de datos + respuestas crudas en `results-orq-<slug>/`.
5. Al terminar, pulsa "Recargar dashboard" y las paginas lo muestran.

## Requisitos
- Python 3.10+ con `streamlit`, `pandas`, `plotly`.
- Un build de llama.cpp con `llama-server` (API OpenAI-compatible) y un modelo GGUF.
- Una api_key de cualquier proveedor OpenAI-compatible para el juez
  (OpenAI, DeepSeek, OpenRouter, Groq, o un Ollama local).

## Configuracion
- `app/config.json`: se crea al pulsar "Guardar configuracion" en Orquestar. Contiene la
  api_key en claro y esta excluida de git. Alternativa: env `OQ_CLOUD_API_KEY` (o
  `DEEPSEEK_API_KEY`) tiene prioridad y no se guarda en disco.
- `OQ_DATA_ROOT`: raiz donde se leen/escriben los informes (por defecto el padre de `app/`).
- Juez local (p.ej. Ollama en 127.0.0.1): pon `"allow_local_cloud": true` en `app/config.json`;
  por defecto solo se aceptan endpoints https publicos (proteccion SSRF).

## Formatos que lee el dashboard
- Multi-ambito: `Benchmark <Modelo> AAAA-MM-DD.md` con puntuacion inline `**(N/5)**`.
- Censura: `results-<slug>-censura.json` (con `tasa_rechazo_pct`) + su md.
- Ciberseguridad: `Benchmark Ciberseguridad <Modelo> AAAA-MM-DD.md` (tabla Prompt/Categoria/Nota).

Los informes son Markdown plano: Obsidian es opcional; sirve cualquier visor o CMS.
""")



def page_orquestador():
    if READ_ONLY:
        st.title("Orquestar (no disponible en el deploy)")
        st.info(
            "El orquestador arranca llama-server en la maquina local y lanza la bateria contra "
            "el modelo. En un servidor remoto no hay GPU ni GGUF, y la api_key no debe viajar "
            "a la infraestructura publica. La vista de resultados (Resumen, Multi-ámbito, "
            "Censura, Subagentes) si funciona: lee el snapshot generado con "
            "`python export_snapshot.py`."
        )
        return

    st.title("Orquestador")
    st.caption(
        "Arranca llama-server, lanza la bateria elegida contra el modelo local, un modelo nube "
        "(cualquier endpoint OpenAI-compatible) juzga cada respuesta y redacta el informe "
        "Markdown en la raiz de datos. El dashboard lo recoge al recargar."
    )

    cfg = orchestrator.load_config()
    c_cfg, l_cfg = cfg["cloud"], cfg["llama"]
    for _k in ("oq_kill_pending", "oq_pending"):
        if _k not in st.session_state:
            st.session_state[_k] = False

    # ---- 1. Estado del servidor: lo primero, porque condiciona todo lo demas ----
    with st.container(border=True):
        st.subheader("1. Estado del servidor")
        port = int(st.session_state.get("oq_port", l_cfg.get("port", 8120)))
        alive = orchestrator.server_alive(port, exe_hint=l_cfg.get("server_exe", ""))
        managed = orchestrator.get_server()
        loading = (not alive) and managed is not None and managed.alive()
        if alive and st.session_state.oq_pending:
            st.session_state.oq_pending = False

        col_state, col_start, col_kill = st.columns([3, 1, 1])
        with col_state:
            if alive:
                model_live = orchestrator.server_model_name(port)
                st.success(f"llama-server ACTIVO en :{port}" +
                           (f" - modelo `{model_live}`" if model_live else ""))
            elif loading:
                st.info("Cargando llama-server... (ver log en vivo, apartado 5)")
            else:
                st.warning(f"llama-server INACTIVO en :{port}")
            gguf_cur = l_cfg.get("gguf", "")
            st.caption(f"GGUF configurado: `{Path(gguf_cur).name if gguf_cur else '(sin GGUF)'}`")
            checked = orchestrator.alive_checked_at(port)
            if checked:
                st.caption(f"Comprobado hace {int(time.time() - checked)} s "
                           f"(cache {int(orchestrator.ALIVE_TTL)} s: comprobar un puerto cerrado "
                           "cuesta ~2 s en Windows).")
        with col_start:
            start_now = st.button("Arrancar llama-server",
                                  help="Levanta llama-server con el GGUF y el puerto guardados en la configuracion")
        with col_kill:
            kill_all = st.button("Matar todos los procesos (liberar GPU)",
                                 help="Termina TODOS los procesos llama.cpp del sistema, no solo los de "
                                      "esta app. Requiere confirmacion")
        if st.button("Recomprobar estado", key="oq_recheck",
                     help="Fuerza la comprobacion del puerto sin esperar a la cache"):
            orchestrator.server_alive(port, force=True, exe_hint=l_cfg.get("server_exe", ""))
            st.rerun()

    # ---- 2. Bateria y parametros de uso diario ----
    st.subheader("2. Bateria y parametros")
    batt = st.selectbox("Bateria", list(orchestrator.BATTERY_LABELS.keys()),
                        format_func=lambda k: orchestrator.BATTERY_LABELS[k], key="oq_batt")
    d = orchestrator.BATTERY_DEFAULTS[batt]
    colA, colB, colC, colD = st.columns(4)
    temp = colA.number_input("Temperature", 0.0, 2.0, step=0.1, value=float(d["temperature"]), key="oq_temp")
    maxtok = colB.number_input("max_tokens", 512, 65536, step=512, value=int(d["max_tokens"]), key="oq_maxtok")
    timeout = colC.number_input("Timeout/prompt (s)", 120, 7200, step=60, value=900, key="oq_timeout")
    limit = colD.number_input("Limitar a N prompts (0 = todos)", 0, 200, value=0, key="oq_limit")
    model_name = st.text_input("Nombre del modelo (titulo del informe)", value="", key="oq_modelname")
    dry = st.checkbox("Modo prueba (sin llama-server ni nube; verifica el pipeline sin escribir ficheros)",
                      key="oq_dry")

    # ---- 3. Configuracion: cerrada por defecto, no interfiere con el uso diario ----
    with st.expander("3. Configuracion (juez nube + llama.cpp)", expanded=False):
        st.caption(
            "Juez: cualquier endpoint OpenAI-compatible (OpenAI, DeepSeek, OpenRouter, Groq, "
            "Ollama local...). La api_key se puede guardar aqui o por env `OQ_CLOUD_API_KEY` "
            "(o `DEEPSEEK_API_KEY`), que tiene prioridad y no queda en disco."
        )
        key_from_env = bool(os.environ.get("OQ_CLOUD_API_KEY") or os.environ.get("DEEPSEEK_API_KEY"))
        key_stored = bool(c_cfg.get("api_key"))
        st.caption(
            "Clave: la prioridad es la env OQ_CLOUD_API_KEY / DEEPSEEK_API_KEY. "
            + ("Env ya definida: no hace falta guardar nada." if key_from_env else
               ("Clave guardada en config.json (en claro). Deja el campo vacio para no tocarla."
                if key_stored else "Sin clave configurada."))
        )
        col1, col2 = st.columns(2)
        with col1:
            base_url = st.text_input("Juez base_url", value=c_cfg.get("base_url", ""), key="oq_base_url")
            api_key = st.text_input("Juez api_key (se deja vacia; solo se guarda si la escribes)",
                                    value="", type="password", key="oq_api_key")
            cloud_model = st.text_input("Juez model", value=c_cfg.get("model", ""), key="oq_model")
            gguf_dir = st.text_input("Carpeta GGUF (para el selector)",
                                     value=l_cfg.get("gguf_dir", ""), key="oq_ggufdir")
        with col2:
            exe = st.text_input("Lanzador (llama-server o .bat/.ps1)",
                                value=l_cfg.get("server_exe", ""), key="oq_exe")
            gguf = st.text_input("GGUF", value=l_cfg.get("gguf", ""), key="oq_gguf")
            port_in = st.number_input("Puerto", 1, 65535, value=int(l_cfg.get("port", 8120)), key="oq_port")
            ctx = st.number_input("ctx (--ctx-size)", 4096, 262144, step=4096,
                                  value=int(l_cfg.get("ctx_size", 16384)), key="oq_ctx")
            extra = st.text_input("Argumentos extra (vacio = ninguno)",
                                  value=l_cfg.get("extra_args", ""), key="oq_extra")
        st.caption("El estado del apartado 1 usa el puerto guardado; cambia aqui el puerto y "
                   "pulsa Guardar para que se compruebe el nuevo.")

        with st.expander("Elegir GGUF de la carpeta modelos"):
            ggufs = orchestrator.list_ggufs(l_cfg.get("gguf_dir", ""))
            if ggufs:
                pick = st.selectbox("GGUF disponibles", [str(p) for p in ggufs], key="oq_gguf_pick")
                st.button("Usar este GGUF",
                          on_click=lambda p=pick: st.session_state.update({"oq_gguf": p}))
            else:
                st.info(f"Sin .gguf en {l_cfg.get('gguf_dir', '')}")

        if st.button("Guardar configuracion"):
            new_cfg = {
                "cloud": {"base_url": base_url, "api_key": api_key or c_cfg.get("api_key", ""),
                          "model": cloud_model},
                "llama": {"server_exe": exe, "gguf": gguf, "port": int(port_in),
                          "ctx_size": int(ctx), "extra_args": extra,
                          "gguf_dir": gguf_dir},
            }
            orchestrator.save_config(new_cfg)
            st.rerun()

    # ---- 4. Ejecutar ----
    st.subheader("4. Ejecutar")

    def _spawn():
        srv, started = orchestrator.start_server(cfg, log=lambda m: None)
        if not started:
            st.info("Ya habia un llama-server vivo gestionado por la app; lo reutilizo.")
        return srv

    def _run_battery(params):
        import subprocess
        app = orchestrator.APP_DIR
        (app / "run-params.json").write_text(json.dumps(params, ensure_ascii=False), encoding="utf-8")
        for f in ("run-progress.json", "run.log"):
            try:
                (app / f).unlink()
            except OSError:
                pass
        logf = open(app / "run.log", "w", encoding="utf-8")
        args = [sys.executable, str(app / "run_battery.py"), "--battery", batt,
                "--params", str(app / "run-params.json"),
                "--progress", str(app / "run-progress.json")]
        if dry:
            args.append("--dry")
        proc = subprocess.Popen(args, cwd=str(app), stdout=logf, stderr=subprocess.STDOUT)
        (app / "run.pid").write_text(str(proc.pid), encoding="utf-8")
        st.session_state["oq_proc"] = proc
        st.session_state["oq_started"] = time.time()
        st.rerun()

    params = {"temperature": float(temp), "max_tokens": int(maxtok),
              "timeout_s": int(timeout), "limit": int(limit),
              "model_name": model_name}

    if st.button("Ejecutar", type="primary"):
        try:
            if dry or alive:
                _run_battery(params)
            else:
                st.session_state.oq_pending = True
        except orchestrator.OrchestratorError as e:
            st.error(str(e))

    if st.session_state.oq_pending and not alive:
        st.warning(f"llama-server no esta activo en :{port}. ¿Arrancar ahora?")
        c_ok, c_no = st.columns(2)
        with c_ok:
            go = st.button("Arrancar llama-server y ejecutar", type="primary")
        with c_no:
            cancel = st.button("Cancelar")
        if cancel:
            st.session_state.oq_pending = False
            st.rerun()
        if go:
            st.session_state.oq_pending = False
            try:
                srv, started = orchestrator.start_server(cfg, log=lambda m: None)
                if started:
                    srv.wait_ready(timeout=1800)
                _run_battery(params)
            except orchestrator.OrchestratorError as e:
                st.error(str(e))

    if kill_all:
        st.session_state.oq_kill_pending = True
        st.rerun()

    if st.session_state.oq_kill_pending:
        st.warning(
            "Esto termina TODOS los procesos llama.cpp del sistema (no solo los gestionados "
            "por esta app), incluido cualquier proceso cuya linea de comando contenga el GGUF "
            "o la carpeta de modelos. Confirmar solo si es lo que quieres."
        )
        c_yes, c_no = st.columns(2)
        with c_yes:
            if st.button("Confirmar", type="primary", key="oq_kill_confirm"):
                st.session_state.oq_kill_pending = False
                done = orchestrator.kill_all_llama(cfg)
                for line in done:
                    st.text(line)
                st.warning("Procesos llama.cpp terminados. Verifica que la GPU queda libre.")
                st.rerun()
        with c_no:
            if st.button("Cancelar", key="oq_kill_cancel"):
                st.session_state.oq_kill_pending = False
                st.rerun()

    if start_now:
        _spawn()
        st.rerun()

    # ---- 5. Progreso y log ----
    st.subheader("5. Progreso y log")

    @st.fragment(run_every=2)
    def _run_status():
        app = orchestrator.APP_DIR
        log_file = app / "run.log"
        prog_file = app / "run-progress.json"
        pid_file = app / "run.pid"
        if not log_file.exists():
            st.caption("No hay bateria en marcha ni log de ejecucion. Ejecuta una bateria en el "
                       "apartado 4 y el progreso aparecera aqui.")
            return
        try:
            prog = json.loads(prog_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            prog = {}

        proc = st.session_state.get("oq_proc")
        if proc is not None:
            vivo = proc.poll() is None
        elif pid_file.exists():
            try:
                vivo = orchestrator.process_alive(int(pid_file.read_text(encoding="utf-8").strip()))
            except (OSError, ValueError):
                vivo = False
        else:
            vivo = False

        frac = min(1.0, max(0.0, float(prog.get("progress") or 0)))
        if prog.get("done"):
            st.caption("Bateria terminada" + (" con error" if prog.get("error") else ""))
        elif vivo:
            started = st.session_state.get("oq_started")
            eta = ""
            if started and frac > 0:
                elapsed = time.time() - started
                eta = f" | ETA {int((elapsed / frac - elapsed) / 60)} min"
            st.caption(f"Bateria en curso: {frac * 100:.0f}%{eta}")
        else:
            st.caption("La bateria no esta corriendo (posible corte)")
        st.progress(frac)

        if vivo:
            if st.button("Parar la bateria", key="oq_stop",
                         help="Termina el proceso de la bateria en marcha (no toca llama-server)"):
                if proc is not None:
                    proc.terminate()
                    st.warning("Bateria parada. El log queda en run.log.")
                else:
                    st.error("No se puede parar: no hay handle del proceso (reinicia Streamlit).")

        try:
            lineas = log_file.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            lineas = []
        with st.expander("Log de la bateria (ultimo bloque)", expanded=False):
            st.code("\n".join(lineas[-400:]), language="text", height=380)
        if prog.get("done"):
            if prog.get("error"):
                st.error(prog["error"])
            else:
                st.success(prog.get("summary", "OK"))
                for pth in prog.get("paths", []):
                    st.text(pth)

    @st.fragment(run_every=2, key="oq_live_log")
    def _live_llama_log():
        st.code(orchestrator.server_tail(300) or "(sin salida de llama-server)", language="text",
                height=380)

    with st.expander("llama-server — log en vivo (auto-refresco 2 s)", expanded=False):
        st.caption("Salida cruda de llama.cpp. Se actualiza sola cada 2 s.")
        clear_log = st.button("Limpiar log (empezar de cero)")
        _live_llama_log()
        if clear_log:
            _clr = getattr(orchestrator, "clear_server_log", None)
            if callable(_clr):
                _clr()
                st.rerun()
            else:
                st.error("Reinicia Streamlit para activar 'Limpiar log' (carga el nuevo orquestador).")

    _run_status()
def page_raw():
    st.title("Datos crudos")
    st.subheader("Multi-ámbito (long)")
    if not mdf.empty:
        st.dataframe(mdf, hide_index=True, width="stretch")

    st.subheader("Censura (JSON por servidor)")
    full = json.dumps(censura, ensure_ascii=False, indent=2)
    for c in censura:
        name = c.get("server", Path(c.get("_fuente", "sin-nombre")).stem)
        with st.expander(name):
            payload = json.dumps(c, ensure_ascii=False, indent=2)
            st.code(payload, language="json")
            st.download_button(f"Descargar {name}.json", data=payload,
                              file_name=f"censura_{Path(name).name}.json",
                              mime="application/json")
    st.download_button("Descargar todo (censura.json)", data=full,
                       file_name="censura_completa.json", mime="application/json")


def page_publicar():
    st.header("Publicar datos")
    st.info("Esta pagina solo existe en la version local. El sitio publico no lleva app: "
           "se sirve un data.json filtrado desde un repo aparte.")
    cfg_path = Path(__file__).resolve().parent / "config.json"
    cfg = json.loads(cfg_path.read_text(encoding="utf-8")) if cfg_path.exists() else {}
    site = cfg.get("site_repo")
    if not site:
        st.error("Falta 'site_repo' en config.json: no se sabe donde va el repo del sitio.")
        return
    st.write("Repo del sitio:", site)
    try:
        reg = sanitizer.load_registry()
        clean, dropped = publish.build_public(reg)
    except SystemExit as e:
        st.error(str(e))
        return
    diff = publish.make_diff(publish.load_prev(site), clean)
    st.write("Modelos publicados:", len(publish.model_set(clean)))
    st.write("Filas:", publish.summary(clean))
    st.write("Campos recortados por el filtrado:", len(dropped))
    st.json(diff)
    if st.button("Publicar (commit + push a Pages)", type="primary"):
        commit = publish.git_publish(site, "datos filtrados")
        st.success(f"Publicado. Commit: {commit}. Pages redeploya en 1-2 minutos.")


pages = [
    st.Page(page_resumen, title="Resumen", url_path="resumen", default=True),
    st.Page(page_censura, title="Censura", url_path="censura"),
    st.Page(page_multi, title="Multi-ámbito", url_path="multi-ambito"),
    st.Page(page_cibersec, title="Ciberseguridad", url_path="ciberseguridad"),
    st.Page(page_subag, title="Subagentes", url_path="subagentes"),
    st.Page(page_scripts, title="Scripts", url_path="scripts"),
    st.Page(page_raw, title="Datos", url_path="datos"),
]
# En el deploy publico no se exponen ni la Guia (explica el orquestador y la
# configuracion local) ni el Orquestador (no hay GPU ni llama-server en Railway).
if not READ_ONLY:
    pages.insert(6, st.Page(page_guia, title="Guia", url_path="guia"))
    pages.insert(7, st.Page(page_orquestador, title="Orquestar", url_path="orquestar"))
    if publish:
        pages.append(st.Page(page_publicar, title="Publicar", url_path="publicar"))

# ---------------------------------------------------------------- iconos (SVG, sin emojis)
ICON_BENCH = (
    '<svg viewBox="0 0 64 64" width="34" height="34" aria-hidden="true">'
    '<rect width="64" height="64" rx="12" fill="#0f1419"/>'
    '<rect x="10" y="38" width="10" height="16" rx="3" fill="#60a5fa"/>'
    '<rect x="24" y="28" width="10" height="26" rx="3" fill="#2dd4bf"/>'
    '<rect x="38" y="16" width="10" height="38" rx="3" fill="#f59e0b"/>'
    '<circle cx="43" cy="10" r="4" fill="#e6edf3"/>'
    "</svg>"
)
ICON_SUN = (
    '<svg viewBox="0 0 24 24" width="18" height="18" fill="none" '
    'stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true">'
    '<circle cx="12" cy="12" r="4.5"/>'
    '<path d="M12 2.5v2M12 19.5v2M2.5 12h2M19.5 12h2M5.2 5.2l1.4 1.4M17.4 17.4l1.4 1.4'
    'M18.8 5.2l-1.4 1.4M6.6 17.4l-1.4 1.4"/>'
    "</svg>"
)
ICON_MOON = (
    '<svg viewBox="0 0 24 24" width="18" height="18" fill="none" '
    'stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true">'
    '<path d="M20.5 13.2A8.5 8.5 0 1 1 11.3 3.5a6.8 6.8 0 0 0 9.2 9.7z"/>'
    "</svg>"
)

# El tema (claro/oscuro) se define en .streamlit/config.toml con [theme.light]
# y [theme.dark]. El CSS no hardcodea colores: toma los del tema ACTIVO, que
# st.context.theme detecta. El boton sol/luna cambia el tema via el parametro
# ?embed_options=... que Streamlit lee al cargar la pagina.
_THEME = (st.context.theme.type or "light")
if _THEME == "dark":
    _BM = {
        "border": "#3a4552",
        "text": "#e6edf3",
        "muted": "#93a1af",
        "accent": "#60a5fa",
        "accent_text": "#0b1220",
        "card": "#1b2229",
        "card_border": "#2a333d",
    }
else:
    _BM = {
        "border": "#c8d2de",
        "text": "#111827",
        "muted": "#4b5563",
        "accent": "#1d4ed8",
        "accent_text": "#ffffff",
        "card": "#ffffff",
        "card_border": "#d7e0ea",
    }

st.markdown(
    f"""
<style>
    :root {{
        --bm-border: {_BM["border"]};
        --bm-text: {_BM["text"]};
        --bm-muted: {_BM["muted"]};
        --bm-accent: {_BM["accent"]};
        --bm-accent-text: {_BM["accent_text"]};
        --bm-card: {_BM["card"]};
        --bm-card-border: {_BM["card_border"]};
    }}
    [data-testid="stTopNavLink"] {{
        text-decoration: none !important;
        border: 1px solid var(--bm-border);
        border-radius: 0.5rem;
        padding: 0.2rem 0.75rem;
    }}
    [data-testid="stTopNavLink"] * {{ color: var(--bm-text) !important; }}
    [data-testid="stTopNavLink"]:hover {{ border-color: var(--bm-accent); }}
    [data-testid="stTopNavLink"]:hover * {{ color: var(--bm-accent) !important; }}
    [data-testid="stTopNavLink"][aria-current="page"] {{
        background: var(--bm-accent);
        border-color: var(--bm-accent);
    }}
    [data-testid="stTopNavLink"][aria-current="page"] * {{ color: var(--bm-accent-text) !important; }}
    [data-testid="stMetricLabel"] {{ color: var(--bm-muted) !important; }}
    .stCaption {{ color: var(--bm-muted) !important; }}
    [data-testid="stMetric"] {{
        background: var(--bm-card);
        border: 1px solid var(--bm-card-border);
        border-radius: 0.75rem;
        padding: 0.9rem 1.1rem;
    }}
    [data-testid="stMetricValue"] {{ color: var(--bm-accent); }}
    .bm-brand {{ display: flex; align-items: center; gap: 0.6rem; margin-bottom: 0.75rem; }}
    .bm-theme-toggle {{
        display: inline-flex; align-items: center; justify-content: center;
        width: 2.1rem; height: 2.1rem; border: 1px solid var(--bm-border);
        border-radius: 0.5rem; color: var(--bm-text); text-decoration: none;
    }}
    .bm-theme-toggle:hover {{ border-color: var(--bm-accent); color: var(--bm-accent); }}
</style>
""",
    unsafe_allow_html=True,
)

_brand, _reload, _theme_btn = st.columns([5, 1, 1])
with _brand:
    st.markdown(
        f'<div class="bm-brand">{ICON_BENCH}'
        '<span style="font-size: 1.5rem; font-weight: 700; color: var(--bm-accent);">'
        'Benchmarks LLM</span>'
        '<span style="font-size: 0.85rem; color: var(--bm-muted); margin-left: 0.75rem;">'
        'Benchmarks de modelos locales</span>'
        "</div>",
        unsafe_allow_html=True,
    )
with _reload:
    st.caption(f"Datos: {st.session_state.data_loaded_at}")
    if st.button("Recargar datos", key="reload_data"):
        get_data.clear()
        st.session_state.data_loaded_at = time.strftime("%Y-%m-%d %H:%M:%S")
        st.rerun()
with _theme_btn:
    _theme = st.context.theme.type or "light"
    _href = "?embed_options=light_theme" if _theme == "dark" else "?embed_options=dark_theme"
    _icon = ICON_SUN if _theme == "dark" else ICON_MOON
    st.markdown(
        f'<a class="bm-theme-toggle" href="{_href}" '
        f'title="Cambiar a tema {"claro" if _theme == "dark" else "oscuro"}" '
        f'aria-label="Cambiar a tema {"claro" if _theme == "dark" else "oscuro"}">{_icon}</a>',
        unsafe_allow_html=True,
    )

nav = st.navigation(pages, position="top")
nav.run()
