CREATE TABLE IF NOT EXISTS servers (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL,
  slug TEXT UNIQUE NOT NULL
);

CREATE TABLE IF NOT EXISTS items (
  id INTEGER PRIMARY KEY,           -- id DofusDB
  name TEXT NOT NULL,
  level INTEGER,
  type_id INTEGER,
  type_name TEXT,
  super_type_id INTEGER,            -- 12=Familier ; ressources identifiées via super_type_name='Ressource'
  super_type_name TEXT,
  icon_id INTEGER,
  icon_url TEXT,
  icon_path TEXT,                   -- cache local du png téléchargé
  phash TEXT,                       -- hash perceptuel précalculé (hex)
  updated_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_items_super_type ON items(super_type_name);
CREATE INDEX IF NOT EXISTS idx_items_type ON items(type_id);

CREATE TABLE IF NOT EXISTS price_snapshots (
  id INTEGER PRIMARY KEY,
  server_id INTEGER NOT NULL REFERENCES servers(id),
  item_id INTEGER NOT NULL REFERENCES items(id),
  lot_size INTEGER NOT NULL CHECK (lot_size IN (1,10,100,1000)),
  pet_level INTEGER,            -- familiers uniquement : niveau réel de l'annonce
                                 -- individuelle (lu dans l'infobulle survolée), à
                                 -- corréler avec le prix de la ligne HDV correspondante
                                 -- car le "prix moyen" du jeu est une moyenne toutes
                                 -- annonces confondues, sans distinction de niveau
  price_kamas INTEGER NOT NULL,
  source TEXT NOT NULL CHECK (source IN ('detail_panel','hdv_list','manual')),
  confidence REAL,
  captured_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_price_item ON price_snapshots(item_id, captured_at);

CREATE TABLE IF NOT EXISTS market_history_points (
  id INTEGER PRIMARY KEY,
  server_id INTEGER NOT NULL REFERENCES servers(id),
  item_id INTEGER NOT NULL REFERENCES items(id),
  period TEXT NOT NULL CHECK (period IN ('24h','7j','30j')),
  point_date TEXT,                  -- inutilisé (prévu pour un point de graphe journalier, jamais exploité — voir CLAUDE.md)
  price_kamas INTEGER,
  quantity_sold INTEGER,
  metric TEXT NOT NULL CHECK (metric IN ('median','mean')),
  source TEXT NOT NULL CHECK (source IN ('ocr_summary')),
  captured_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_history_item ON market_history_points(item_id, period);

CREATE TABLE IF NOT EXISTS jobs (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL,
  icon_url TEXT
);

CREATE TABLE IF NOT EXISTS recipes (
  id INTEGER PRIMARY KEY,           -- id DofusDB
  result_item_id INTEGER NOT NULL REFERENCES items(id),
  job_id INTEGER REFERENCES jobs(id),
  level INTEGER,
  xp INTEGER,
  updated_at TEXT
);

CREATE TABLE IF NOT EXISTS recipe_ingredients (
  recipe_id INTEGER NOT NULL REFERENCES recipes(id),
  ingredient_item_id INTEGER NOT NULL,   -- pas de FK stricte : certains ingrédients (ex. équipements/ressources hors scope) peuvent manquer de la table items
  quantity INTEGER NOT NULL,
  PRIMARY KEY (recipe_id, ingredient_item_id)
);

CREATE TABLE IF NOT EXISTS pet_feed_xp (           -- référence, seedée une fois (recherche
  id INTEGER PRIMARY KEY,                          -- externe ou dérivée d'un event observé)
  resource_item_id INTEGER NOT NULL UNIQUE REFERENCES items(id),
  xp_value INTEGER NOT NULL,
  source TEXT NOT NULL DEFAULT 'vision_llm_seed',
  updated_at TEXT
);

CREATE TABLE IF NOT EXISTS pet_feed_events (       -- observations réelles capturées à l'écran
  id INTEGER PRIMARY KEY,
  server_id INTEGER NOT NULL REFERENCES servers(id),
  pet_name TEXT NOT NULL,
  pet_level INTEGER,
  resource_item_id INTEGER NOT NULL REFERENCES items(id),
  quantity INTEGER NOT NULL,
  xp_gained INTEGER,
  captured_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS app_config (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS tracked_trades (      -- suivi manuel achat/craft -> revente,
  id INTEGER PRIMARY KEY,                        -- indépendant de price_snapshots (une
  server_id INTEGER NOT NULL REFERENCES servers(id),  -- transaction réelle de l'utilisateur,
  item_id INTEGER NOT NULL REFERENCES items(id),      -- pas un relevé HDV)
  quantity INTEGER NOT NULL CHECK (quantity > 0),
  buy_price_kamas INTEGER NOT NULL,   -- coût total payé pour `quantity` unités
  sell_price_kamas INTEGER,           -- revenu total encaissé, NULL tant que non vendu
  status TEXT NOT NULL CHECK (status IN ('holding','sold')) DEFAULT 'holding',
  -- étape d'une position "holding" : 'forge' = équipement en cours de
  -- forgemagie (on saisit le coût en runes avant mise en vente), 'live' = en
  -- vente. Non contraint par CHECK exprès (ajout de colonne non destructif sur
  -- les bases existantes). Ignoré quand status='sold'.
  stage TEXT NOT NULL DEFAULT 'live',
  rune_cost_kamas INTEGER,   -- coût de forgemagie en kamas (équipement), ajouté au coût d'achat
  created_at TEXT NOT NULL,
  sold_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_tracked_trades_server ON tracked_trades(server_id, status);

-- Atelier : file de préparation AVANT le suivi réel (tracked_trades). On y met
-- un équipement (à crafter) ou un familier (à monter) ; une fois les ressources
-- réunies / le familier au niveau voulu, il passe dans tracked_trades.
CREATE TABLE IF NOT EXISTS atelier_items (
  id INTEGER PRIMARY KEY,
  server_id INTEGER NOT NULL REFERENCES servers(id),
  item_id INTEGER NOT NULL REFERENCES items(id),
  kind TEXT NOT NULL CHECK (kind IN ('equipment','pet')),
  quantity INTEGER NOT NULL DEFAULT 1,                 -- équipement : nombre de crafts prévus (met à l'échelle les ressources)
  feed_resource_item_id INTEGER REFERENCES items(id),  -- familier : ressource de nourrissage choisie
  feed_qty_owned INTEGER NOT NULL DEFAULT 0,           -- familier : quantité de cette ressource déjà possédée
  created_at TEXT NOT NULL,
  UNIQUE (server_id, item_id, kind)
);
CREATE INDEX IF NOT EXISTS idx_atelier_items_server ON atelier_items(server_id, kind);

-- Quantité de ressources possédées, GLOBALE par ressource (pas par équipement) :
-- la liste de courses de l'atelier agrège les besoins de tous les équipements,
-- l'utilisateur saisit une seule fois combien il a de chaque ressource.
CREATE TABLE IF NOT EXISTS atelier_owned_resources (
  server_id INTEGER NOT NULL REFERENCES servers(id),
  resource_item_id INTEGER NOT NULL REFERENCES items(id),
  quantity_owned INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (server_id, resource_item_id)
);
