"""Test del pacchetto: import leggero (le anteprime fzf sono processi separati) e API pubblica."""

import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def run_python(code: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, cwd=ROOT, timeout=30)


class TestLightweightImport(unittest.TestCase):
    """Ogni anteprima di fzf avvia un nuovo interprete: non deve caricare requests/bs4."""

    def test_preview_module_does_not_import_the_http_stack(self) -> None:
        result = run_python(
            "import sys, ani_it.preview; "
            "bad = [m for m in ('requests', 'bs4', 'ani_it.scraper', 'ani_it.cli', 'ani_it.resolver') if m in sys.modules]; "
            "print(bad)"
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "[]")

    def test_importing_the_package_alone_is_cheap(self) -> None:
        result = run_python("import sys, ani_it; print('requests' in sys.modules or 'bs4' in sys.modules)")
        self.assertEqual(result.stdout.strip(), "False")


class TestPublicApiStillWorks(unittest.TestCase):
    def test_lazy_exports_resolve(self) -> None:
        import ani_it

        for name in ani_it.__all__:
            with self.subTest(name=name):
                self.assertIsNotNone(getattr(ani_it, name))

    def test_unknown_attribute_raises(self) -> None:
        import ani_it

        with self.assertRaises(AttributeError):
            ani_it.does_not_exist  # noqa: B018

    def test_version_is_a_string(self) -> None:
        import ani_it

        self.assertRegex(ani_it.__version__, r"^\d+\.\d+\.\d+")


if __name__ == "__main__":
    unittest.main()
