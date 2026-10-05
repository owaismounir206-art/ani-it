"""Test della directory runtime (socket IPC): permessi, proprietario, symlink."""

import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ani_it import utils


class TestRuntimeDir(unittest.TestCase):
    """get_runtime_dir deve restituire una directory privata (0700) di proprietà dell'utente."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.base = Path(self.temp_dir.name)

        env = patch.dict(os.environ, {}, clear=False)
        env.start()
        self.addCleanup(env.stop)
        os.environ.pop("XDG_RUNTIME_DIR", None)

        base_patch = patch.object(utils, "FALLBACK_RUNTIME_BASE", self.base)
        base_patch.start()
        self.addCleanup(base_patch.stop)

        # Le directory di ripiego (mkdtemp) finiscono nella cartella del test, non in /tmp
        real_mkdtemp = tempfile.mkdtemp
        mkdtemp_patch = patch.object(
            utils.tempfile, "mkdtemp", side_effect=lambda **kw: real_mkdtemp(dir=self.base, **kw)
        )
        mkdtemp_patch.start()
        self.addCleanup(mkdtemp_patch.stop)

    @staticmethod
    def mode(path: Path) -> int:
        return stat.S_IMODE(path.stat().st_mode)

    def test_fallback_dir_is_created_private(self) -> None:
        path = utils.get_runtime_dir()
        self.assertEqual(path, self.base / f"ani-it-{os.getuid()}")
        self.assertEqual(self.mode(path), 0o700)

    def test_xdg_runtime_dir_is_created_private(self) -> None:
        os.environ["XDG_RUNTIME_DIR"] = str(self.base / "xdg")
        (self.base / "xdg").mkdir()
        path = utils.get_runtime_dir()
        self.assertEqual(path, self.base / "xdg" / "ani-it")
        self.assertEqual(self.mode(path), 0o700)

    def test_existing_permissive_dir_owned_by_user_is_tightened(self) -> None:
        loose = self.base / f"ani-it-{os.getuid()}"
        loose.mkdir(mode=0o755)
        loose.chmod(0o755)
        path = utils.get_runtime_dir()
        self.assertEqual(path, loose)
        self.assertEqual(self.mode(path), 0o700)

    def test_symlink_is_never_used(self) -> None:
        """Un symlink predisposto da un altro utente non deve diventare la directory dei socket."""
        target = self.base / "attacker-dir"
        target.mkdir()
        (self.base / f"ani-it-{os.getuid()}").symlink_to(target)

        path = utils.get_runtime_dir()
        self.assertNotEqual(path.resolve(), target.resolve())
        self.assertFalse(path.is_symlink())
        self.assertEqual(self.mode(path), 0o700)

    def test_dir_owned_by_someone_else_is_not_used(self) -> None:
        squatted = self.base / f"ani-it-{os.getuid()}"
        squatted.mkdir(mode=0o700)
        with patch.object(utils.os, "getuid", return_value=os.getuid() + 1):
            # getuid() falsificato: la directory esiste ma risulta di un altro utente
            path = utils.get_runtime_dir()
        self.assertNotEqual(path, squatted)
        self.assertEqual(self.mode(path), 0o700)


if __name__ == "__main__":
    unittest.main()
