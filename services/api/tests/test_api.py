"""Tests d'API hermétiques : pagination, auth par scopes, rate-limit, lecture.

Ne couvrent PAS la vision (c'est le collector) ni la logique métier profonde
(déjà testée par la suite racine sur données réelles) — ils valident la couche
HTTP ajoutée en phase 3/4 : le wrapper SQLAlchemy, les scopes, la pagination,
le limiteur.
"""
from __future__ import annotations

READ = {"X-API-Key": "KEY_READ"}
OWNER = {"X-API-Key": "KEY_OWNER"}


# ── lecture de base (mode ouvert) ─────────────────────────────────────────
def test_status_ok(client):
    r = client.get("/v1/status")
    assert r.status_code == 200
    assert r.json()["last_capture_at"] is not None


def test_public_reads_ok(client):
    for path in ["/v1/settings", "/v1/servers", "/v1/dashboard", "/v1/crafts",
                 "/v1/pets", "/v1/prices?category=ressource", "/v1/history/available",
                 "/v1/suivis", "/v1/atelier"]:
        assert client.get(path).status_code == 200, path


# ── pagination du catalogue ───────────────────────────────────────────────
def test_items_pagination(client):
    r = client.get("/v1/items?limit=2&offset=0")
    body = r.json()
    assert r.status_code == 200
    assert len(body["data"]) == 2
    assert body["pagination"]["total"] == 11  # 5 base + 2 craftés + 4 carburants
    assert body["pagination"]["next_offset"] == 2
    # dernière page
    r2 = client.get("/v1/items?limit=2&offset=10")
    assert len(r2.json()["data"]) == 1
    assert r2.json()["pagination"]["next_offset"] is None


def test_items_filter_by_name_and_type(client):
    assert client.get("/v1/items?q=piou").json()["pagination"]["total"] == 1
    assert client.get("/v1/items?super_type=Ressource").json()["pagination"]["total"] == 3


def test_items_limit_bounds(client):
    assert client.get("/v1/items?limit=0").status_code == 422   # < 1
    assert client.get("/v1/items?limit=999").status_code == 422  # > 200


# ── auth par scopes (mode strict) ─────────────────────────────────────────
def test_auth_required_without_key(client, auth_on):
    assert client.get("/v1/prices?category=ressource").status_code == 401


def test_auth_invalid_key(client, auth_on):
    assert client.get("/v1/prices?category=ressource", headers={"X-API-Key": "NOPE"}).status_code == 401


def test_read_key_grants_public(client, auth_on):
    assert client.get("/v1/prices?category=ressource", headers=READ).status_code == 200


def test_read_key_denied_on_owner(client, auth_on):
    # scope read ne couvre pas owner -> 403
    assert client.get("/v1/suivis", headers=READ).status_code == 403


def test_owner_key_grants_owner_and_read(client, auth_on):
    assert client.get("/v1/suivis", headers=OWNER).status_code == 200
    assert client.get("/v1/prices?category=ressource", headers=OWNER).status_code == 200


def test_owner_write_requires_owner(client, auth_on):
    # écriture (router writes) sous scope owner
    assert client.post("/v1/settings", data={"safety_margin_percent": "6"}, headers=READ).status_code == 403
    assert client.post("/v1/settings", data={"safety_margin_percent": "6"}, headers=OWNER).status_code == 200


# ── filtres Crafts (métier + intervalle de niveau) ────────────────────────
def _craft_result_ids(body):
    return {m["result_item_id"] for m in body["crafts"]} | {m["result_item_id"] for m in body["incomplete"]}


def test_crafts_jobs_list(client):
    names = {j["name"] for j in client.get("/v1/crafts").json()["jobs"]}
    assert {"Tailleur", "Cordonnier"} <= names


def test_crafts_filter_by_job(client):
    assert _craft_result_ids(client.get("/v1/crafts?job_id=1").json()) == {10, 11}
    assert _craft_result_ids(client.get("/v1/crafts?job_id=2").json()) == {3}


def test_crafts_shows_cost_only_recipe(client):
    # recette 100 (Cape, item 10) : Blé pricé mais pas de prix de vente de la
    # Cape -> apparaît en calculable avec un coût, marge None.
    crafts = client.get("/v1/crafts?job_id=1").json()["crafts"]
    ids = {m["result_item_id"] for m in crafts}
    assert 10 in ids
    cape = next(m for m in crafts if m["result_item_id"] == 10)
    assert cape["craft_cost"] is not None
    assert cape["margin"] is None


def test_crafts_filter_by_level(client):
    assert _craft_result_ids(client.get("/v1/crafts?job_id=1&level_min=60").json()) == {11}
    assert _craft_result_ids(client.get("/v1/crafts?job_id=1&level_max=60").json()) == {10}


def test_crafts_inverted_interval_is_swapped(client):
    # min > max : l'API échange les bornes plutôt que de rejeter -> 10..80
    assert _craft_result_ids(client.get("/v1/crafts?job_id=1&level_min=80&level_max=10").json()) == {10, 11}


def test_crafts_negative_level_rejected(client):
    assert client.get("/v1/crafts?level_min=-5").status_code == 422


# ── Élevage (remplissage des jauges d'enclos) ─────────────────────────────
def test_elevage_caresseur_cheapest_fill_with_craft_vs_buy(client):
    body = client.get("/v1/elevage").json()
    car = next(g for g in body["gauges"] if g["gauge"] == "Caresseur")
    assert car["fillable"] is True
    # 33348 est CRAFTABLE (Blé à 10 < achat HDV 100) -> réf 10, cpp 0.002 :
    # 40000*0.002 + 30000*0.04 + 20000*0.06 + 10000*0.08 = 80 + 1200 + 1200 + 800
    assert car["total_cost"] == 3280
    assert [s["item_id"] for s in car["segments"]] == [33348, 33400, 33453, 33499]
    # 1re tranche = crafter (moins cher que d'acheter), les autres = acheter
    assert [s["source"] for s in car["segments"]] == ["craft", "buy", "buy", "buy"]


def test_elevage_unpriced_gauge_not_fillable(client):
    body = client.get("/v1/elevage").json()
    baf = next(g for g in body["gauges"] if g["gauge"] == "Baffeur")
    assert baf["fillable"] is False
    assert baf["total_cost"] is None
    assert body["total_all"] is None  # toutes les jauges ne sont pas chiffrables


def test_elevage_custom_range_70k_90k(client):
    # intervalle 70 000 → 90 000 : seule la bande [70k,90k] compte (plafond>=90k
    # -> carburant 33453, 300/5000 = 0.06/point) -> 20000 * 0.06 = 1200.
    body = client.get("/v1/elevage?range_from=70000&range_to=90000").json()
    assert body["range_from"] == 70000 and body["range_to"] == 90000
    car = next(g for g in body["gauges"] if g["gauge"] == "Caresseur")
    assert car["fillable"] is True
    assert car["total_cost"] == 1200
    assert [s["item_id"] for s in car["segments"]] == [33453]


def test_elevage_range_from_gt_to_is_swapped(client):
    body = client.get("/v1/elevage?range_from=90000&range_to=70000").json()
    assert body["range_from"] == 70000 and body["range_to"] == 90000


def test_elevage_range_out_of_bounds_rejected(client):
    assert client.get("/v1/elevage?range_to=200000").status_code == 422
    assert client.get("/v1/elevage?range_from=-5").status_code == 422


# ── OpenAPI / Swagger (vitrine) ───────────────────────────────────────────
def test_swagger_and_security_scheme(client):
    assert client.get("/v1/docs").status_code == 200
    spec = client.get("/v1/openapi.json").json()
    schemes = spec.get("components", {}).get("securitySchemes", {})
    # le schéma X-API-Key est déclaré (bouton Authorize + cadenas dans Swagger)
    assert any(s.get("name") == "X-API-Key" for s in schemes.values())
    assert spec["info"]["version"] == "1.0.0"


# ── rate-limit ────────────────────────────────────────────────────────────
def test_rate_limit_trips(client, monkeypatch):
    monkeypatch.setenv("KAMAS_RATE_LIMIT", "3")
    monkeypatch.setenv("KAMAS_RATE_WINDOW", "60")
    h = {"X-API-Key": "RL_BUCKET"}  # bucket isolé par clé
    codes = [client.get("/v1/status", headers=h).status_code for _ in range(4)]
    assert codes[:3] == [200, 200, 200]
    assert codes[3] == 429


# ── Suivis : forgemagie (équipement) + regroupement par objet ──────────────
def _clear_trades(client):
    body = client.get("/v1/suivis").json()
    for t in body.get("forgemagie", []):
        client.post(f"/v1/suivis/{t['id']}/delete")
    for key in ("holding_groups", "sold_groups"):
        for g in body.get(key, []):
            for t in g["trades"]:
                client.post(f"/v1/suivis/{t['id']}/delete")


def test_suivis_equipment_goes_to_forgemagie_then_confirm(client):
    _clear_trades(client)
    # item 3 = Amulette (équipement) -> doit atterrir en forgemagie, pas en cours
    assert client.post("/v1/suivis", data={"item_id": "3", "quantity": "1", "buy_price_kamas": "1000"}).json()["ok"]
    body = client.get("/v1/suivis").json()
    assert body["forge_count"] == 1 and body["holding_count"] == 0
    tid = body["forgemagie"][0]["id"]
    # confirmer avec 500 runes -> passe En cours, coût effectif = 1500
    assert client.post(f"/v1/suivis/{tid}/forge-confirm", data={"rune_cost_kamas": "500"}).json()["ok"]
    body2 = client.get("/v1/suivis").json()
    assert body2["forge_count"] == 0 and body2["holding_count"] == 1
    grp = body2["holding_groups"][0]
    assert grp["total_buy"] == 1500 and grp["trades"][0]["rune_cost_kamas"] == 500
    _clear_trades(client)


def test_suivis_resource_direct_and_grouped_with_sell(client):
    _clear_trades(client)
    # item 1 = Ressource -> direct En cours (pas de forge)
    client.post("/v1/suivis", data={"item_id": "1", "quantity": "2", "buy_price_kamas": "100"})
    client.post("/v1/suivis", data={"item_id": "1", "quantity": "3", "buy_price_kamas": "150"})
    body = client.get("/v1/suivis").json()
    assert body["forge_count"] == 0
    grp = next(g for g in body["holding_groups"] if g["item_id"] == 1)
    # chaque exemplaire est DISSOCIÉ (qty 2 + qty 3 -> 5 positions de quantité 1,
    # coût 250 réparti à l'unité) : chacune a son propre prix de vente.
    assert grp["n_trades"] == 5 and grp["total_quantity"] == 5 and grp["total_buy"] == 250
    assert all(t["quantity"] == 1 for t in grp["trades"])
    # vendre un exemplaire -> lui seul quitte "en cours" pour "vendus"
    client.post(f"/v1/suivis/{grp['trades'][0]['id']}/sell", data={"sell_price_kamas": "200"})
    body2 = client.get("/v1/suivis").json()
    assert next(g for g in body2["holding_groups"] if g["item_id"] == 1)["n_trades"] == 4
    assert next(g for g in body2["sold_groups"] if g["item_id"] == 1)["n_trades"] == 1
    _clear_trades(client)


def test_suivis_reopen_sold_back_to_holding(client):
    _clear_trades(client)
    client.post("/v1/suivis", data={"item_id": "1", "quantity": "1", "buy_price_kamas": "100"})
    tid = client.get("/v1/suivis").json()["holding_groups"][0]["trades"][0]["id"]
    client.post(f"/v1/suivis/{tid}/sell", data={"sell_price_kamas": "200"})
    assert client.get("/v1/suivis").json()["sold_count"] == 1
    # remettre en cours -> repasse en holding/live, prix de vente conservé comme visé
    assert client.post(f"/v1/suivis/{tid}/reopen").json()["ok"]
    body = client.get("/v1/suivis").json()
    assert body["sold_count"] == 0 and body["holding_count"] == 1
    assert body["holding_groups"][0]["trades"][0]["sell_price_kamas"] == 200
    _clear_trades(client)


def test_suivis_potential_profit(client):
    _clear_trades(client)
    # position en cours avec prix visé 150 et coût 100 -> potentiel +50
    client.post("/v1/suivis", data={"item_id": "1", "quantity": "1", "buy_price_kamas": "100", "sell_price_kamas": "150"})
    # une autre sans prix visé -> n'entre ni dans le potentiel ni dans le CA
    client.post("/v1/suivis", data={"item_id": "1", "quantity": "1", "buy_price_kamas": "80"})
    body = client.get("/v1/suivis").json()
    assert body["total_potential"] == 50           # 150 - 100
    assert body["total_potential_revenue"] == 150  # CA à la revente = prix visé
    _clear_trades(client)


def test_crafts_flags_already_engaged(client):
    _clear_trades(client)
    body = client.get("/v1/crafts").json()
    assert all(k in body for k in ("in_atelier", "in_forge", "in_holding"))
    # un équipement suivi (item 10 = Cape) part en forgemagie -> in_forge
    client.post("/v1/suivis", data={"item_id": "10", "quantity": "1", "buy_price_kamas": "100"})
    assert 10 in client.get("/v1/crafts").json()["in_forge"]
    _clear_trades(client)
