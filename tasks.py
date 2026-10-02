"""Point d'entrée unique pour toutes les commandes de dev — équivalent d'un
Makefile, en Python pur (stdlib seulement) pour marcher pareil depuis
PowerShell, Git Bash ou cmd sans dépendre d'un outil externe (`make` n'est
pas installé par défaut sous Windows).

Usage : python tasks.py <commande> [args...]  (voir DEV.md pour la liste)
"""
from __future__ import annotations

import argparse
import os
import platform
import subprocess
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
VENV_DIR = ROOT / ".venv"
VENV_PYTHON = VENV_DIR / ("Scripts/python.exe" if platform.system() == "Windows" else "bin/python")


def _venv_python() -> Path:
    if not VENV_PYTHON.exists():
        sys.exit("[tasks] venv introuvable — lance d'abord : python tasks.py setup")
    return VENV_PYTHON


def _run(cmd: list[str], cwd: Path = ROOT) -> None:
    print(f"[tasks] {' '.join(cmd)}")
    result = subprocess.run(cmd, cwd=cwd)
    if result.returncode != 0:
        sys.exit(result.returncode)


def cmd_setup(_args: list[str]) -> None:
    if not VENV_PYTHON.exists():
        _run([sys.executable, "-m", "venv", str(VENV_DIR)])
    _run([str(VENV_PYTHON), "-m", "pip", "install", "--upgrade", "pip"])
    _run([str(VENV_PYTHON), "-m", "pip", "install", "-r", str(ROOT / "requirements.txt")])


def cmd_sync(args: list[str]) -> None:
    """Peuple la base locale (items / recettes / métiers + icônes) depuis l'API
    DofusDB. À lancer une fois sur une machine neuve, avant `stack`."""
    _run([str(_venv_python()), str(ROOT / "src/sync/dofusdb_sync.py"), *args])


def cmd_test(args: list[str]) -> None:
    """Suite de tests hermétiques de la Price API (scopes, pagination,
    rate-limit, migrations up/down, rentabilité via HTTP)."""
    _run([str(_venv_python()), "-m", "pytest", "-q", "tests", *args], cwd=ROOT / "services/api")


def cmd_stack(args: list[str]) -> None:
    """Lance toute l'archi en services :
      • API   (Price API)  http://127.0.0.1:8100  (Swagger sur /v1/docs)
      • Front (site)       http://127.0.0.1:8200

    Par DÉFAUT : tout tourne dans CETTE console, sortie préfixée [api]/[front],
    Ctrl+C arrête tout. `stack -- --windows` ouvre une console par service.
    Mode dev : auth désactivée, rate-limit désactivé."""
    python = str(_venv_python())
    windows_mode = "--windows" in args or "-w" in args

    base_env = {**os.environ, "PYTHONUNBUFFERED": "1"}
    services = [
        ("api", [python, "-m", "uvicorn", "app.main:app", "--app-dir", str(ROOT / "services/api"),
                 "--port", "8100", "--log-level", "warning"],
         {**base_env, "KAMAS_RATE_LIMIT": "0"}),
        ("front", [python, "-m", "uvicorn", "app.main:app", "--app-dir", str(ROOT / "services/front"),
                   "--port", "8200", "--log-level", "warning"],
         {**base_env, "KAMAS_API_URL": "http://127.0.0.1:8100"}),
    ]

    urls = "[tasks] Site : http://127.0.0.1:8200   |   API+Swagger : http://127.0.0.1:8100/v1/docs"

    if windows_mode and platform.system() == "Windows":
        for _name, cmd, env in services:
            subprocess.Popen(cmd, cwd=ROOT, env=env, creationflags=subprocess.CREATE_NEW_CONSOLE)
        print("[tasks] stack lancée en fenêtres séparées. " + urls)
        return

    print(urls)
    print("[tasks] tout dans cette fenêtre — Ctrl+C pour tout arrêter.\n")
    running: list[tuple[str, subprocess.Popen]] = []

    def _pump(name: str, proc: subprocess.Popen) -> None:
        assert proc.stdout is not None
        for line in proc.stdout:
            sys.stdout.write(f"[{name}] {line}")
            sys.stdout.flush()

    try:
        for name, cmd, env in services:
            proc = subprocess.Popen(
                cmd, cwd=ROOT, env=env,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1,
            )
            running.append((name, proc))
            threading.Thread(target=_pump, args=(name, proc), daemon=True).start()
        while any(p.poll() is None for _n, p in running):
            time.sleep(0.5)
    except KeyboardInterrupt:
        print("\n[tasks] arrêt en cours...")
    finally:
        for _name, proc in running:
            if proc.poll() is None:
                proc.terminate()
        for _name, proc in running:
            try:
                proc.wait(timeout=5)
            except Exception:
                proc.kill()
    print("[tasks] arrêté.")


COMMANDS = {
    "setup": cmd_setup,
    "sync": cmd_sync,
    "test": cmd_test,
    "stack": cmd_stack,
}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=sorted(COMMANDS))
    parser.add_argument("extra", nargs=argparse.REMAINDER)
    parsed = parser.parse_args()
    COMMANDS[parsed.command](parsed.extra)


if __name__ == "__main__":
    main()
