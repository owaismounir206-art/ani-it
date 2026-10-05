"""Test dei file di completamento shell: coerenza con la CLI e sicurezza dei titoli."""

import argparse
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from ani_it.cli import create_parser

ROOT = Path(__file__).resolve().parent.parent
COMPLETIONS = ROOT / "completions"
BASH_COMPLETION = Path("/usr/share/bash-completion/bash_completion")


def parser_options() -> set[str]:
    """Opzioni pubbliche del parser (quelle con help soppresso sono interne)."""
    parser = create_parser()
    return {
        opt
        for action in parser._actions
        if action.help != argparse.SUPPRESS
        for opt in action.option_strings
    }


class TestCompletionsMatchCli(unittest.TestCase):
    """Ogni opzione della CLI deve comparire nei completamenti, senza descrizioni sbagliate."""

    def test_bash_options_match_parser(self) -> None:
        text = (COMPLETIONS / "ani-it.bash").read_text(encoding="utf-8")
        opts = set(re.search(r'local opts="([^"]+)"', text).group(1).split())
        self.assertEqual(opts, parser_options())

    def test_fish_declares_every_option(self) -> None:
        text = (COMPLETIONS / "ani-it.fish").read_text(encoding="utf-8")
        declared = set()
        for line in text.splitlines():
            if not line.startswith("complete -c ani-it"):
                continue
            declared.update(f"-{m}" for m in re.findall(r"\s-s\s+(\w)", line))
            declared.update(f"--{m}" for m in re.findall(r"\s-l\s+([\w-]+)", line))
        self.assertEqual(declared, parser_options())

    def test_zsh_declares_every_option(self) -> None:
        text = (COMPLETIONS / "_ani-it.zsh").read_text(encoding="utf-8")
        for option in parser_options():
            with self.subTest(option=option):
                self.assertRegex(text, rf"(?<![\w-]){re.escape(option)}(?![\w-])")

    def test_short_h_is_not_documented_as_help(self) -> None:
        """Nella CLI `-h` apre la cronologia: solo `--help` mostra l'aiuto."""
        for name in ("ani-it.bash", "ani-it.fish", "_ani-it.zsh"):
            text = (COMPLETIONS / name).read_text(encoding="utf-8")
            for line in text.splitlines():
                short_h = re.search(r"(?<![\w-])-h(?![\w-])", line) or re.search(r"-s\s+h(?!\w)", line)  # zsh/bash | fish
                if short_h and "aiuto" in line.lower():
                    self.fail(f"{name}: '-h' descritto come aiuto: {line.strip()}")


@unittest.skipUnless(shutil.which("bash") and BASH_COMPLETION.exists(), "bash o bash-completion non disponibili")
class TestBashCompletionIsSafe(unittest.TestCase):
    """I titoli in cronologia arrivano dall'API del sito: non devono mai essere eseguiti."""

    def run_completion(self, titles: list[str], typed: str = "") -> tuple[list[str], list[str]]:
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            fake = work / "ani-it"
            printed = "\n".join(t.replace("'", "'\\''") for t in titles)
            fake.write_text(
                "#!/usr/bin/env bash\n"
                f"if [ \"$1\" = \"--list-history-titles\" ]; then printf '%s\\n' '{printed}'; fi\n"
            )
            fake.chmod(0o755)
            script = (
                f'cd "{work}"; export PATH="{work}:$PATH"\n'
                f"source {BASH_COMPLETION}\n"
                f"source {COMPLETIONS / 'ani-it.bash'}\n"
                f'COMP_WORDS=(ani-it "{typed}"); COMP_CWORD=1; COMP_LINE="ani-it {typed}"; COMP_POINT=${{#COMP_LINE}}\n'
                "_ani_it_completions\n"
                'printf "%s\\n" "${COMPREPLY[@]}"\n'
            )
            result = subprocess.run(["bash", "-c", script], capture_output=True, text=True, timeout=20)
            created = sorted(p.name for p in work.iterdir() if p.name != "ani-it")
            return result.stdout.splitlines(), created

    def test_command_substitution_in_title_is_not_executed(self) -> None:
        replies, created = self.run_completion(["Evil $(touch PWNED)", "Naruto"])
        self.assertEqual(created, [], "il completamento ha eseguito un comando contenuto in un titolo")
        self.assertIn(r"Evil\ \$\(touch\ PWNED\)", replies)

    def test_backticks_are_not_executed(self) -> None:
        _, created = self.run_completion(["Evil `touch PWNED2`"])
        self.assertEqual(created, [])

    def test_titles_are_quoted_so_they_stay_one_argument(self) -> None:
        replies, _ = self.run_completion(["Frieren's Journey", "Naruto Shippuden"])
        self.assertIn(r"Frieren\'s\ Journey", replies)
        self.assertIn(r"Naruto\ Shippuden", replies)

    def test_prefix_filtering(self) -> None:
        replies, _ = self.run_completion(["Naruto", "Bleach", "Naruto Shippuden"], typed="Nar")
        self.assertEqual(sorted(replies), sorted(["Naruto", r"Naruto\ Shippuden"]))


class TestSyntax(unittest.TestCase):
    @unittest.skipUnless(shutil.which("bash"), "bash non disponibile")
    def test_bash_syntax(self) -> None:
        result = subprocess.run(["bash", "-n", str(COMPLETIONS / "ani-it.bash")], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    @unittest.skipUnless(shutil.which("zsh"), "zsh non disponibile")
    def test_zsh_syntax(self) -> None:
        result = subprocess.run(["zsh", "-n", str(COMPLETIONS / "_ani-it.zsh")], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    @unittest.skipUnless(shutil.which("fish"), "fish non disponibile")
    def test_fish_syntax(self) -> None:
        result = subprocess.run(["fish", "-n", str(COMPLETIONS / "ani-it.fish")], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
