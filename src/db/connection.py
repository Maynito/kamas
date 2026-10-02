"""Point d'entrée unique pour ouvrir une connexion à la base SQLite du
projet. Centralise le chemin de la DB et active PRAGMA foreign_keys, que
SQLite n'applique PAS par défaut malgré les clauses REFERENCES du schéma
(schema.sql) — sans ce pragma, les contraintes de clé étrangère déclarées
ne sont que de la documentation, jamais vérifiées.

Mode WAL également activé : le watcher (écritures continues,
price_snapshots à chaque capture) et l'app web (lectures pour le dashboard)
ouvrent chacun leur propre connexion sur le MÊME fichier, potentiellement en
même temps (cf. `tasks.py dev`, qui lance les deux ensemble). En mode
journal par défaut (rollback), un writer bloque tous les readers pendant
toute la transaction ; en WAL, les lecteurs ne sont jamais bloqués par un
writer en cours — sans ça, l'app web peut lever "database is locked" pile
au moment où le watcher enregistre un relevé. `synchronous=NORMAL` est la
combinaison recommandée avec WAL (sûr contre un crash applicatif, seul un
crash de l'OS/panne de courant en plein milieu d'un checkpoint pourrait
perdre les toutes dernières écritures — risque jugé acceptable pour un
outil de suivi de prix, pas un registre transactionnel).
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).resolve().parents[2] / "data" / "kamas.db"


def get_connection(db_path: Path | str = DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    return conn
