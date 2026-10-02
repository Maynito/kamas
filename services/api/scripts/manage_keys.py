"""Gestion des clés API de la Price API (phase 4).

La clé brute n'est affichée QU'UNE FOIS, à la création — seul son SHA-256 est
stocké. Cible la base pointée par DATABASE_URL (repli SQLite dev).

Usage :
    python scripts/manage_keys.py create --scope read  --label "site du pote"
    python scripts/manage_keys.py create --scope owner --label "front"
    python scripts/manage_keys.py list
    python scripts/manage_keys.py revoke --id 3
"""
from __future__ import annotations

import argparse
import secrets
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # services/api sur le path

from app.auth import hash_key  # noqa: E402
from app.db import get_conn  # noqa: E402


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def create(scope: str, label: str) -> None:
    if scope not in ("read", "owner", "admin"):
        raise SystemExit("scope invalide (read | owner | admin)")
    raw = f"kamas_{scope}_{secrets.token_urlsafe(24)}"
    conn = get_conn()
    conn.execute(
        "INSERT INTO api_keys (key_hash, scope, label, created_at, revoked) VALUES (?, ?, ?, ?, 0)",
        (hash_key(raw), scope, label, _now()),
    )
    conn.commit()
    conn.close()
    print("Clé créée (copie-la maintenant, elle ne sera plus affichée) :")
    print(f"  scope : {scope}")
    print(f"  label : {label}")
    print(f"  clé   : {raw}")
    print("\nÀ passer en en-tête HTTP :  X-API-Key: " + raw)


def list_keys() -> None:
    conn = get_conn()
    rows = conn.execute(
        "SELECT id, scope, label, created_at, revoked FROM api_keys ORDER BY id"
    ).fetchall()
    conn.close()
    if not rows:
        print("(aucune clé)")
        return
    print(f"{'id':>3}  {'scope':<6} {'révoquée':<8} {'label'}")
    for r in rows:
        print(f"{r[0]:>3}  {r[1]:<6} {'oui' if r[4] else 'non':<8} {r[2] or ''}  (créée {r[3]})")


def revoke(key_id: int) -> None:
    conn = get_conn()
    conn.execute("UPDATE api_keys SET revoked = 1 WHERE id = ?", (key_id,))
    conn.commit()
    conn.close()
    print(f"clé {key_id} révoquée.")


def main() -> None:
    p = argparse.ArgumentParser(description="Gestion des clés API Kamas")
    sub = p.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("create", help="créer une clé")
    c.add_argument("--scope", required=True, choices=["read", "owner", "admin"])
    c.add_argument("--label", default="")
    sub.add_parser("list", help="lister les clés")
    r = sub.add_parser("revoke", help="révoquer une clé")
    r.add_argument("--id", type=int, required=True)
    args = p.parse_args()

    if args.cmd == "create":
        create(args.scope, args.label)
    elif args.cmd == "list":
        list_keys()
    elif args.cmd == "revoke":
        revoke(args.id)


if __name__ == "__main__":
    main()
