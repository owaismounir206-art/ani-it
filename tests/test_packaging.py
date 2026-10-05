"""Coerenza del packaging: versione unica, licenza, PKGBUILD e .SRCINFO allineati."""

import importlib.util
import re
import shutil
import subprocess
import sys
import tempfile
import tomllib
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def read(name: str) -> str:
    return (ROOT / name).read_text(encoding="utf-8")


def pyproject() -> dict:
    return tomllib.loads(read("pyproject.toml"))


class TestSingleVersionSource(unittest.TestCase):
    """La versione vive in ani_it/_version.py; tutto il resto la deriva o viene verificato."""

    def test_code_constants_follow_the_version_file(self) -> None:
        import ani_it
        from ani_it import _version, constants

        self.assertEqual(ani_it.__version__, _version.__version__)
        self.assertEqual(constants.APP_VERSION, _version.__version__)

    def test_pyproject_reads_the_version_dynamically(self) -> None:
        project = pyproject()
        self.assertIn("version", project["project"].get("dynamic", []))
        self.assertNotIn("version", project["project"])
        self.assertEqual(project["tool"]["setuptools"]["dynamic"]["version"], {"attr": "ani_it._version.__version__"})

    def test_pkgbuild_and_srcinfo_match_the_version_file(self) -> None:
        from ani_it import _version

        pkgbuild = read("PKGBUILD")
        srcinfo = read(".SRCINFO")
        self.assertEqual(re.search(r"^pkgver=(\S+)", pkgbuild, re.M).group(1), _version.__version__)
        self.assertEqual(re.search(r"^\s*pkgver = (\S+)", srcinfo, re.M).group(1), _version.__version__)
        self.assertEqual(
            re.search(r"^pkgrel=(\S+)", pkgbuild, re.M).group(1),
            re.search(r"^\s*pkgrel = (\S+)", srcinfo, re.M).group(1),
        )

    def test_project_url_is_the_same_everywhere(self) -> None:
        pkgbuild_url = re.search(r'^url="([^"]+)"', read("PKGBUILD"), re.M).group(1)
        srcinfo_url = re.search(r"^\s*url = (\S+)", read(".SRCINFO"), re.M).group(1)
        self.assertEqual(pkgbuild_url, pyproject()["project"]["urls"]["Homepage"])
        self.assertEqual(srcinfo_url, pkgbuild_url)


class TestBuildConfiguration(unittest.TestCase):
    def test_setup_cfg_is_gone(self) -> None:
        """Duplicava la configurazione di pyproject.toml."""
        self.assertFalse((ROOT / "setup.cfg").exists())

    def test_license_uses_spdx_expression_not_deprecated_table(self) -> None:
        project = pyproject()["project"]
        self.assertEqual(project["license"], "GPL-3.0-or-later")
        self.assertEqual(project["license-files"], ["LICENSE"])
        classifiers = project.get("classifiers", [])
        self.assertFalse([c for c in classifiers if c.startswith("License ::")], "classifier di licenza deprecato")

    def test_runtime_dependencies_are_only_requests_and_bs4(self) -> None:
        names = {re.split(r"[<>=!~ ]", d)[0] for d in pyproject()["project"]["dependencies"]}
        self.assertEqual(names, {"requests", "beautifulsoup4"})

    def test_ruff_is_configured(self) -> None:
        ruff = pyproject()["tool"]["ruff"]
        self.assertEqual(ruff["target-version"], "py312")
        self.assertIn("F", ruff["lint"]["select"])


class TestLicenseFile(unittest.TestCase):
    """La LICENSE era troncata (245 righe invece di 674)."""

    def test_full_gplv3_text(self) -> None:
        text = read("LICENSE")
        self.assertGreaterEqual(len(text.splitlines()), 660)
        for marker in (
            "GNU GENERAL PUBLIC LICENSE",
            "Version 3, 29 June 2007",
            "TERMS AND CONDITIONS",
            "17. Interpretation of Sections 15 and 16.",
            "END OF TERMS AND CONDITIONS",
            "How to Apply These Terms to Your New Programs",
            "<https://www.gnu.org/licenses/>",
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker, text)

    def test_all_numbered_sections_are_present(self) -> None:
        text = read("LICENSE")
        for number in range(0, 18):
            with self.subTest(section=number):
                self.assertRegex(text, rf"(?m)^\s*{number}\. [A-Z]")


class TestPkgbuild(unittest.TestCase):
    """Il PKGBUILD deve essere riproducibile e adatto ad AUR."""

    def setUp(self) -> None:
        self.text = read("PKGBUILD")

    def test_source_is_declared_and_does_not_use_startdir(self) -> None:
        self.assertRegex(self.text, r"(?m)^source=\(\s*\S")
        self.assertNotIn("$startdir", self.text)
        self.assertNotRegex(self.text, r"(?m)^source=\(\)")

    def test_source_is_pinned_to_the_release_tag(self) -> None:
        self.assertRegex(self.text, r"#tag=v\$pkgver")

    def test_checksums_are_declared(self) -> None:
        self.assertRegex(self.text, r"(?m)^sha256sums=\(\s*'[^']+'\s*\)")

    def test_has_build_check_and_package_functions(self) -> None:
        for function in ("build", "check", "package"):
            with self.subTest(function=function):
                self.assertRegex(self.text, rf"(?m)^{function}\(\) \{{")

    def test_check_runs_the_test_suite(self) -> None:
        check_body = self.text.split("check() {", 1)[1].split("\n}", 1)[0]
        self.assertIn("unittest discover", check_body)

    def test_git_is_a_make_dependency(self) -> None:
        self.assertRegex(self.text, r"makedepends=\([^)]*'git'")

    @unittest.skipUnless(shutil.which("bash"), "bash non disponibile")
    def test_pkgbuild_is_valid_bash(self) -> None:
        result = subprocess.run(["bash", "-n", str(ROOT / "PKGBUILD")], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    @unittest.skipUnless(shutil.which("makepkg"), "makepkg non disponibile")
    def test_srcinfo_is_in_sync_with_pkgbuild(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            shutil.copy(ROOT / "PKGBUILD", tmp)
            result = subprocess.run(["makepkg", "--printsrcinfo"], cwd=tmp, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), read(".SRCINFO").strip())


@unittest.skipUnless(
    importlib.util.find_spec("build") and importlib.util.find_spec("setuptools"),
    "build/setuptools non disponibili",
)
class TestWheelBuild(unittest.TestCase):
    """Costruisce davvero il wheel in una copia temporanea (niente file nell'albero dei sorgenti)."""

    def test_wheel_metadata_follows_the_single_version_source(self) -> None:
        from ani_it import _version

        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp) / "src"
            shutil.copytree(
                ROOT, work,
                ignore=shutil.ignore_patterns(
                    ".git", "build", "dist", "pkg", "src", "*.egg-info", "__pycache__", "*.pkg.tar.*", ".venv"
                ),
            )
            out = Path(tmp) / "out"
            result = subprocess.run(
                [sys.executable, "-m", "build", "--wheel", "--no-isolation", "--outdir", str(out)],
                cwd=work, capture_output=True, text=True, timeout=240,
            )
            self.assertEqual(result.returncode, 0, result.stdout[-2000:] + result.stderr[-2000:])

            wheels = list(out.glob("*.whl"))
            self.assertEqual(len(wheels), 1)
            self.assertIn(f"-{_version.__version__}-", wheels[0].name)

            with zipfile.ZipFile(wheels[0]) as wheel:
                names = wheel.namelist()
                metadata = next(wheel.read(n).decode() for n in names if n.endswith(".dist-info/METADATA"))
            self.assertIn(f"Version: {_version.__version__}", metadata)
            self.assertIn("License-Expression: GPL-3.0-or-later", metadata)
            self.assertTrue([n for n in names if n.endswith("licenses/LICENSE")], "LICENSE assente nel wheel")
            self.assertFalse([n for n in names if n.startswith("tests/")], "i test non vanno nel wheel")
            self.assertIn("ani_it/preview.py", names)


if __name__ == "__main__":
    unittest.main()
