"""Smoke test de la UI: cada pagina debe ejecutarse sin lanzar excepciones.

Requiere streamlit. Si no esta instalado, el test se omite (la suite base es
stdlib y sigue funcionando sin el entorno de la app).

    python -m unittest discover -s tests -v
"""
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

try:
    import streamlit  # noqa: F401
    HAS_STREAMLIT = True
except ImportError:
    HAS_STREAMLIT = False


@unittest.skipUnless(HAS_STREAMLIT, "streamlit no instalado: smoke test de UI omitido")
class TestPages(unittest.TestCase):
    def test_todas_las_paginas_ejecutan_sin_excepcion(self):
        import app
        pages = ["page_resumen", "page_censura", "page_multi", "page_cibersec",
                 "page_subag", "page_scripts", "page_guia", "page_orquestador",
                 "page_raw", "page_publicar"]
        for name in pages:
            fn = getattr(app, name, None)
            if fn is None:
                self.fail(f"app.py no define {name}")
            try:
                fn()
            except Exception as e:  # noqa: BLE001
                self.fail(f"{name} lanzo {type(e).__name__}: {e}")

    def test_no_quedan_filtros_en_la_sidebar(self):
        """Los filtros de Multi-ambito deben ir inline, no en la sidebar."""
        src = (REPO / "app.py").read_text(encoding="utf-8")
        self.assertNotIn("st.sidebar", src,
                        "la sidebar plegada oculta los filtros: ponlos inline")

    def test_multi_df_descarta_medidas_que_no_son_aciertos(self):
        """Un score no puede superar su maximo: 'Len (tok)' mide tokens, no acierto."""
        import app
        rows = [
            {"model": "X", "test": "T1", "title": "t", "domain": "Texto",
             "score": 4, "max": 5, "fuente": "f.md", "fecha": "2026-01-01",
             "source_tag": "directo"},
            {"model": "Len (tok)", "test": "T1", "title": "t", "domain": "Texto",
             "score": 1262, "max": 5, "fuente": "f.md", "fecha": "2026-01-01",
             "source_tag": "directo"},
        ]
        df = app.multi_df(rows)
        self.assertEqual(len(df), 2)
        self.assertEqual(list(df["es_acierto"]), [True, False])
        self.assertEqual(df.loc[1, "pct"], 25240.0)

    def test_no_hay_polling_con_tasklist_en_la_ui(self):
        """El estado de la bateria se comprueba con proc.poll(), no con tasklist."""
        src = (REPO / "app.py").read_text(encoding="utf-8")
        self.assertNotIn("tasklist", src,
                        "tasklist en la UI significa subprocesos cada 2 s")


if __name__ == "__main__":
    unittest.main()
