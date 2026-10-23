---
created: 2026-09-15
modified: 2026-09-15
tags: [entornos-ia, readme]
---

# Benchmarks LLM

Dashboard Streamlit para visualizar y orquestar benchmarks de modelos de lenguaje
locales (llama.cpp) y de nube. Lee informes en Markdown plano y los convierte en
graficas interactivas: ranking por puntuacion, heatmaps por dominio, desglose por
dominio, censura, ciberseguridad ofensiva y ejecucion de subagentes.

Los datos son contenido externo: la herramienta no depende de Obsidian ni de ningun
editor concreto. Sirve cualquier carpeta de ficheros Markdown + JSON que siga el
esquema documentado en `DATA-FORMATOS.md`.

## Funcionalidades

- **Resumen**: inventario de fuentes y volumen de datos cargados.
- **Multi-ambito**: filtros inline (fuente, modelos, pruebas minimas), ranking con
  recuento de pruebas, **vista global** (heatmap modelo x dominio, o la matriz completa
  modelo x prueba), **comparativa lado a lado** (radar por dominio + tabla por prueba
  ordenada por discriminacion) y un heatmap pequeno por dominio. Las filas con
  score > maximo (medidas de tokens, no de acierto) se excluyen y se avisan.
- **Censura**: tasa de rechazo por servidor y por categoria, heatmap de rechazos.
- **Ciberseguridad**: comparativas e individuales de prompts ofensivos (solo lectura).
- **Subagentes**: resultados de ejecuciones de agentes (exitCode, duracion, stopReason).
- **Scripts**: visor de los scripts de benchmark (`run-*.ps1`, `bench_*.py`).
- **Orquestar**: ordenado por uso real (estado del servidor, bateria, configuracion
  cerrada, ejecutar, progreso). Arranca `llama-server`, lanza la bateria, un juez
  nube puntua cada respuesta y redacta el informe Markdown en la raiz de datos. Hay
  ETA, boton de parada y progreso sin subprocesos cada 2 s.
- **Tema**: claro y oscuro definidos en `.streamlit/config.toml` (`[theme.light]` y
  `[theme.dark]`). Se cambia con el icono sol/luna de la cabecera (enlaza
  `?embed_options=light_theme` / `?embed_options=dark_theme`) o desde Settings > Theme.
  El CSS de `app.py` no fija colores: toma los del tema activo.

## Instalacion

Requisitos: Python 3.10+.

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
# Linux/macOS: source .venv/bin/activate
pip install -r requirements.txt
```

`requirements.lock` es el freeze exacto del entorno que ejecuta la app
(`.venvs\benchmarks` en la maquina de referencia): usalo si quieres reproducir el
entorno preciso.

## Ejecucion

```bash
streamlit run app.py
```

Abre http://localhost:8501. Con el venv existente:

```powershell
& ".venvs\benchmarks\Scripts\python.exe" -m streamlit run app.py --server.port 8501
```

## Datos

La app lee la **raiz de datos**, resuelta en este orden (ver `paths.py`):

1. La variable de entorno `OQ_DATA_ROOT`, si esta definida.
2. La clave `data_root` de `config.json`.
3. Un directorio `data/` junto al repo, si no defines nada.

La raiz de datos vive fuera del repo y los informes siguen en Obsidian. Si la raiz
no existe, el dashboard lo dice explicitamente en vez de mostrar secciones vacias.

Para probar la app con los datos ficticios incluidos en `examples/`:

```bash
# Windows (PowerShell)
$env:OQ_DATA_ROOT = "examples"
streamlit run app.py

# Linux/macOS
OQ_DATA_ROOT=examples streamlit run app.py
```

Sin datos la app arranca igual y avisa de que la raiz no existe. El formato exacto
de cada fichero esta documentado en la nota `DATA-FORMATOS.md` del vault del
proyecto (no viaja en este repo).

## Pruebas

Sin dependencias extra (unittest de la stdlib) y sin tocar los datos reales:

```bash
python -m unittest discover -s tests -v
```

Cubren los tres contratos de formato (multi-ambito, censura, ciberseguridad), el
parser de baterias, las defensas SSRF/redirecciones, el guard de raiz, el saneado
de slug y la comprobacion de procesos. Anadir tests cuando se cambie un formato:
 el historico de 455 filas depende de que el parser no se rompa.

## Configuracion del orquestador

La configuracion vive en `config.json` (excluido de git). Copia la plantilla:

```bash
copy config.example.json config.json
```

Campos:

- `cloud`: endpoint OpenAI-compatible del juez (`base_url`, `model`, `api_key`).
- `llama`: lanzador de llama.cpp (`server_exe`, `gguf`, `gguf_dir`, `port`,
  `ctx_size`, `extra_args`).
- `allow_local_cloud`: ponlo en `true` solo si el juez es local (p. ej. Ollama en
  127.0.0.1). Por defecto solo se aceptan endpoints HTTPS publicos (proteccion
  contra SSRF).

La `api_key` tambien se puede inyectar por variable de entorno, que tiene prioridad
y no queda escrita en disco: `OQ_CLOUD_API_KEY` o `DEEPSEEK_API_KEY`.

No cometas `config.json` ni tu clave de API.

## Publicar datos (Cloudflare Pages)

La herramienta se usa en local. Lo que se publica no es la app, sino un artefacto
estatico generado a partir de ella, en un repo aparte (`benchmarks-site`) y detras
de Cloudflare Access.

Piezas implicadas:

- `model_registry.json` (local, gitignoreado): alias de modelos descensurados (UC)
  y ofensivos (OF), artefactos de parsing y vetos.
- `sanitizer.py`: elimina respuestas crudas, rutas, puertos y fuentes; agrega los
  scripts; recorta cualquier string a 200 caracteres.
- `publish.py`: regenera, filtra, compara con lo ya publicado y, tras confirmacion,
  hace commit + push al repo del sitio.
- `deploy_pages.py`: crea el proyecto de Pages, el dominio y la app de Access por API.
- `.git/hooks/pre-push` (local): bloquea un push cuyo arbol contenga nombres reales.

```bash
python publish.py --dry-run     # informe: modelos, filas, diff
python publish.py               # confirma y publica
python sanitizer.py --check data_snapshot/snapshot_public.json.gz
python sanitizer.py --check-tree
```

Reglas que se cumplen:

- El snapshot completo nunca se versiona: `data_snapshot/` esta en `.gitignore`.
- Los modelos descensurados salen como `Qwen-27B-UC-01`, `Qwen-7B-OF-04`, etc.
- La pagina de scripts publica totales y lenguajes, no nombres, rutas, puertos ni fuente.
- En el sitio no hay Guia ni Orquestar: no existe app, solo HTML y `data.json`.

El `Dockerfile` sigue sirviendo para levantar la app Streamlit en un contenedor
propio con `BM_READONLY=1` y tus propios datos. No se usa para el sitio publico.

## Uso responsable

La pagina de ciberseguridad y la bateria de censura contienen material ofensivo
destinado a pruebas de seguridad autorizadas y a evaluar la censura de modelos.
Usalo solo en entornos y objetivos donde tengas autorizacion explicita.

## Licencia

MIT. Ver [LICENSE](LICENSE).
