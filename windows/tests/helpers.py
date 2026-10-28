"""Loads the tools by path, as `py -3 -I` would, and builds throwaway folders."""
import importlib.util
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parent.parent


def load(filename, name=None):
    spec = importlib.util.spec_from_file_location(name or filename.replace("-", "_")[:-3], ROOT / "duo" / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def git(folder, *args):
    return subprocess.run(["git", "-C", str(folder), *args], capture_output=True, text=True, check=True,
                          stdin=subprocess.DEVNULL).stdout.strip()


def make_repo(folder):
    folder.mkdir(parents=True, exist_ok=True)
    git(folder, "init", "-q", "-b", "main")
    git(folder, "config", "user.email", "test@example.com")
    git(folder, "config", "user.name", "Test")
    git(folder, "config", "core.autocrlf", "false")
    (folder / "README.md").write_text("hello\n", encoding="utf-8")
    git(folder, "add", "README.md")
    git(folder, "commit", "-q", "-m", "first")
    return folder
