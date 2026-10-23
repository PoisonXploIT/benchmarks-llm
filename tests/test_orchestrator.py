"""Tests de seguridad y robustez del orquestador (sin red, sin arrancar modelos).

    python -m unittest discover -s tests -v
"""
import sys
import tempfile
import unittest
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

import orchestrator  # noqa: E402


class TestProcessAlive(unittest.TestCase):
    """process_alive es el respaldo cuando la UI no tiene el handle del proceso.
    Debe dar True para un PID propio y False para uno inexistente."""

    def test_pid_propio_vivo(self):
        import os
        self.assertTrue(orchestrator.process_alive(os.getpid()))

    def test_pid_inexistente_no_vivo(self):
        self.assertFalse(orchestrator.process_alive(999999))

    def test_pid_invalido_no_explode(self):
        for bad in (0, -1, None):
            try:
                self.assertFalse(orchestrator.process_alive(bad))
            except Exception as e:  # noqa: BLE001
                self.fail(f"process_alive({bad}) lanzo {type(e).__name__}: {e}")


class TestSSRF(unittest.TestCase):
    def test_loopback_blocked_por_defecto(self):
        with self.assertRaises(orchestrator.OrchestratorError):
            orchestrator.validate_cloud_url("http://127.0.0.1:8120")

    def test_loopback_permitido_si_se_declara_local(self):
        orchestrator.validate_cloud_url("http://127.0.0.1:8120", allow_local=True)

    def test_rangos_privados_y_link_local_bloqueados(self):
        for url in ("http://10.0.0.5/v1", "http://192.168.1.1/v1",
                    "http://169.254.169.254/v1", "http://[::1]/v1"):
            with self.assertRaises(orchestrator.OrchestratorError):
                orchestrator.validate_cloud_url(url)

    def test_scheme_sin_host_invalido(self):
        for url in ("", "ftp://api.example.com", "api.example.com"):
            with self.assertRaises(orchestrator.OrchestratorError):
                orchestrator.validate_cloud_url(url)

    def test_redirecciones_no_se_siguen(self):
        handler = orchestrator._NoRedirectHandler()
        req = urllib.request.Request("https://juez.example.com/v1/chat/completions")
        with self.assertRaises(orchestrator.OrchestratorError):
            handler.redirect_request(req, None, 302, "Found", {},
                                     "http://127.0.0.1:8120/v1/chat/completions")

    def test_opener_no_acepta_redirects_por_defecto(self):
        redirects = [h for h in orchestrator._OPENER.handlers
                     if isinstance(h, urllib.request.HTTPRedirectHandler)]
        self.assertTrue(redirects)
        self.assertTrue(all(isinstance(h, orchestrator._NoRedirectHandler)
                            for h in redirects))


class TestEscritura(unittest.TestCase):
    def test_assert_in_root_rechaza_fuera_de_la_raiz(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            orig = orchestrator.ROOT
            orchestrator.ROOT = root
            try:
                orchestrator._assert_in_root(root / "informe.md")
                orchestrator._assert_in_root(root / "sub" / "informe.md")
                with self.assertRaises(orchestrator.OrchestratorError):
                    orchestrator._assert_in_root(root.parent / "fuera.md")
            finally:
                orchestrator.ROOT = orig

    def test_slug_no_permite_rutas(self):
        self.assertEqual(orchestrator._slug("Modelo/../Malicioso 2026"),
                         "modelo-malicioso-2026")


class TestArgs(unittest.TestCase):
    def test_rutas_windows_no_se_rompen(self):
        args = orchestrator.split_extra_args(
            '--mmproj C:\\Users\\usuario\\AI\\models\\proj.gguf -ngl 999'
        )
        self.assertEqual(args[1], "C:\\Users\\usuario\\AI\\models\\proj.gguf")
        self.assertEqual(args[2:], ["-ngl", "999"])


class TestBaterias(unittest.TestCase):
    def test_bateria_multi_from_markdown(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            orig = orchestrator.ROOT
            orchestrator.ROOT = root
            try:
                (root / "ENUNCIADOS-benchmark.md").write_text(
                    "## Codigo\n\n### C1 — Debug sutil\nEscribe el fix.\n\n---\n\n"
                    "## Razonamiento\n\n### R1 — Calculo\n2+2?\n",
                    encoding="utf-8",
                )
                tests = orchestrator.load_battery("multi")
                self.assertEqual(len(tests), 2)
                self.assertEqual(tests[0]["domain"], "Codigo")
                self.assertEqual(tests[0]["id"], "C1")
                self.assertIn("Escribe el fix", tests[0]["text"])
            finally:
                orchestrator.ROOT = orig


if __name__ == "__main__":
    unittest.main()
