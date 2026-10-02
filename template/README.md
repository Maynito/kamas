# Handoff: Kamas — dashboard de suivi de prix Dofus

## Overview
Refonte complète du dashboard perso "Kamas" (site interne FastAPI + Jinja2, un seul utilisateur, usage desktop en parallèle du jeu Dofus). L'app suit les prix du marché, calcule la rentabilité de crafts/familiers, et permet un suivi manuel achat→revente.

## About the Design Files
**Les fichiers de ce dossier sont des références de design en HTML** (prototypes visuels et interactifs construits avec un framework interne de composants ("DCLogic"/templates), PAS du code de production à copier tel quel. La tâche est de **recréer ce design dans l'environnement existant du projet** (FastAPI + Jinja2 côté rendu serveur, JS côté client pour l'interactivité — cmd+k, tri de tableaux, modales, toasts) en respectant ses conventions déjà en place. Si des choix d'architecture front restent à faire, privilégier des solutions légères cohérentes avec une stack Jinja2 (vanilla JS / Alpine.js / htmx), sans introduire un framework SPA lourd sauf si déjà présent dans le projet.

## Fidelity
**Haute fidélité (hifi)** : couleurs exactes, typographie, espacements et interactions sont définitifs et doivent être reproduits fidèlement. Les données affichées (noms d'objets "Objet A/B/C…", prix, etc.) sont des **placeholders génériques** à remplacer par les vraies données du modèle (items Dofus réels, prix réels du watcher, etc.) — ne pas copier ces valeurs telles quelles.

## Screens / Views

Toutes les pages partagent une **coquille commune** : sidebar fixe à gauche (222px) + zone de contenu à droite, le tout centré dans la fenêtre (jamais collé aux bords), sur un fond extérieur très sombre.

### Shell commun
- Conteneur externe : `min-height:100vh`, `display:flex; align-items:flex-start; justify-content:center; padding:36px 20px`, fond `#140f09`.
- Carte app : `width:min(1560px,100%)`, `min-height:900px`, `border-radius:8px`, `box-shadow:0 24px 60px rgba(0,0,0,.45)`, fond `#241a10` avec un **quadrillage subtil** (grid overlay) via deux `linear-gradient` de `#34261a` à 1px, `background-size:24px 24px`. Layout `display:grid; grid-template-columns:222px 1fr`.
- Sidebar : fond `#14100a`, bordure droite `#090604`, padding `20px 12px`. Logo "KAMAS" en haut (Space Mono 700, 18px, `#f7f0e2`). Liste de nav (9 items, voir ci-dessous). En bas (`margin-top:auto`) : bloc "Contexte actif" (switcher serveur/catégorie, fond `#201509`, bordure `#3a2a12`, cliquable) puis indicateur watcher (point vert `#f0b429` + texte "Watcher actif · capture {fraîcheur}").
- Items de nav (Dashboard, Crafts, Familiers, Prix, Historique, Suivis, Serveurs, Réglages, Import) : actif = fond `#3a2a12` + texte or `#f0b429` + gras 700 ; inactif = texte `#b3a184`, hover fond `#201509`.
- Topbar (haut de la zone contenu) : titre d'écran à gauche (22px/800, couleur `#f0e6d2` — clair car sur fond sombre), barre de recherche/cmd+k à droite (280px, fond `#f7f0e2`, bordure `#c9b48f`, badge `⌘K` à droite).

### 1. Dashboard
- **But** : vue d'ensemble en un coup d'œil — combien d'objets rentables, fraîcheur des données, et 3 mini-tableaux classés.
- Layout : colonne verticale, gap 20px.
  - Ligne de 3 KPI cards (grid 3 colonnes, gap 16px) : "Crafts rentables" (compteur, 44px gras, vert `#2e7d32`), "Familiers rentables" (idem), "Dernière capture" (fond transparent — délibérément moins mis en avant — texte clair `#f0e6d2`/`#c9b48f` car il n'est PAS dans une carte crème).
  - Grid 2 colonnes (1.3fr/1fr) × 2 rangées : "Crafts les plus rentables" (occupe les 2 rangées de la 1ère colonne, tableau 4 colonnes Objet/Marge/Bénéf.mois/action), "Familiers rentables" (3 colonnes), "Activité récente" (liste texte + timestamp).
  - Chaque ligne de tableau a un bouton "+ Suivre" (vert, ouvre la modale de suivi sans changer d'écran).
  - Tooltip natif (`title`) sur les objets avec ingrédients manquants.

### 2. Crafts
- Barre : champ recherche (280px) + compteur de résultats + "{N} crafts réalisables dès maintenant" (vert, aligné à droite).
- Tableau principal (7 colonnes) : Objet, Coût craft (or `#b8791a`), Prix vente (or), Marge/u (or — c'est un montant en kamas), Vol./mois (neutre), **Bénéf./mois** (dominant : 19px/800, vert), action "+ Suivre". En-têtes cliquables pour trier (état de tri géré en state, flèche ↓/↑ sur la colonne active).
- Icône ⚠ rouge (`#a3271a`) à côté du nom si ingrédients manquants (tooltip natif donne le détail).
- **Tableau "Recettes incomplètes"** (sous le tableau principal) : colonnes Objet à craft / Ressource (badge coloré + petit chiffre "xN" = quantité nécessaire) / Qté / Raison. Badge vert-tint (`bg:#dcead9, fg:#2e7d32`) = ressource déjà référencée (juste en attente des autres) ; badge rouge-tint (`bg:#f0d6cf, fg:#a3271a`) = ressource manquante en inventaire OU prix non référencé — le texte "Raison" précise laquelle des deux causes s'applique. En-têtes triables.

### 3. Familiers
- Même schéma que Crafts mais sans colonne Marge/u (6 colonnes : Familier, Coût, Prix vente, Vol./mois, Bénéf./mois, action).
- **Tableau "En attente de référencement"** en dessous : familiers repérés mais pas assez de données, avec un badge rouge-tint "Raison" (ex : "Aucune vente observée", "Prix de vente non référencé", "Coût d'obtention inconnu").

### 4. Prix
- Onglets segmentés (vrai composant, pas des boutons isolés) : Ressources / Familiers / Équipements (fond `#ecdfc2`, actif = fond vert `#2e7d32` + texte crème).
- Champ recherche + indicateur "● Mise à jour auto" (point or, à droite).
- Tableau 3 colonnes : Objet, Dernier prix (or), Capturé (timestamp relatif).
- Bouton "Afficher plus" centré sous le tableau si plus de résultats que la limite affichée (pagination par tranche de 5, remise à zéro au changement d'onglet/recherche).

### 5. Historique
- Layout 2 colonnes : panneau gauche (260px, liste d'objets filtrable) + panneau droit (détail).
- Panneau gauche : mêmes onglets segmentés Ressources/Familiers/Équipements qu'en Prix (persistants — changer de catégorie NE perd PAS la sélection en cours), champ recherche, liste cliquable (actif = fond `#3a2a12` + texte or).
- Panneau droit : nom de l'objet sélectionné (clair car hors carte), 3 KPI (Cours médian/moyen en or, Vendus/semaine en vert), puis **contenu différent selon le type** :
  - **Ressource** : tableau "Prix par lot + XP familier" — colonnes Lot (x1/x10/x100/x1000), Prix (or), XP donné, bouton "Modifier" par ligne.
  - **Familier** : tableau "Prix par niveau" — Niveau 0 / Niveau 100, Prix (or), bouton "Modifier".
  - **Équipement** : un seul bloc "Dernier prix" (or, 22px) + bouton "Modifier".
- En bas : formulaire de saisie manuelle — "Nouveau prix (kamas)" + "XP obtenu" (uniquement visible pour les ressources) + bouton "Enregistrer" (vert).

### 6. Suivis
- 4 KPI cards : Bénéfice total (vert), ROI global (vert), En cours (neutre), Vendus (neutre).
- Onglets segmentés : Tous / En cours / Vendus + champ recherche.
- Tableau 7 colonnes : Objet, Qté, Payé (or), Vente (or), Bénéfice (vert, triable), ROI (vert, triable), Statut (badge : "En cours" = teal `bg:#cfe3e8, fg:#1f5a66` ; "Vendu" = vert-tint `bg:#dcead9, fg:#2e7d32`).

### 7. Serveurs
- Liste de cards (max-width 640px), une par contexte/serveur : nom + méta (ex. "2 personnages · créé il y a 4 mois"), badge "Actif" (vert plein) si actif sinon bouton "Activer" (outline).
- Actions par ligne : **Renommer** (bascule la card en mode édition avec un `<input>` + bouton "Valider") et **Supprimer** (texte rouge).
- En bas : champ + bouton "+ Créer" pour ajouter un nouveau contexte.

### 8. Réglages
- Card "Marge de sécurité" : slider 0–30% (accent vert) + valeur affichée à droite.
- Card "Taxe HDV" : valeur fixe "2%", lecture seule, non modifiable.

### 9. Import manuel
- Texte d'intro (clair, hors carte) expliquant l'usage (secours du watcher auto).
- Zone de dépôt d'image (280px de haut, bordure pointillée) — utilise le composant `<image-slot>` (voir `image-slot.js`), à remplacer côté prod par un vrai input file / drag-and-drop.
- Card "Imports récents" : liste texte + timestamp.

## Interactions & Behavior

- **Cmd+K (recherche globale)** : raccourci clavier global (`Cmd/Ctrl+K`), ouvre une modale centrée (overlay `rgba(10,13,5,.55)`) avec un champ de recherche auto-focus et une liste de résultats mêlant objets ("objet") et pages ("page"). `Escape` ferme la modale (et toute autre modale ouverte).
- **Modale "+ Suivre"** : ouverte depuis n'importe quelle ligne de tableau (Dashboard, Crafts, Familiers) SANS changer d'écran ni perdre le tri/filtre en cours (c'est le point clé de la refonte — l'ancien flux redirigeait vers une page séparée). Affiche le prix suggéré ET sa source (ex. "Suggéré depuis le prix médian HDV des N derniers jours"), champs Quantité + Prix payé, boutons Annuler/Confirmer.
- **Toast de confirmation** : après une action (confirmer un suivi, enregistrer un prix, créer/supprimer un serveur), un toast sombre apparaît en bas centré pendant ~2.4s puis disparaît automatiquement.
- **Tri de tableaux** : cliquer un en-tête de colonne trie (asc/desc, toggle au clic répété) — implémenté sur Crafts, "Recettes incomplètes" et Suivis. Un indicateur ↓/↑ apparaît sur la colonne triée (Crafts).
- **Filtre/recherche texte** : champ de recherche par écran, filtre en live sur le nom (Crafts, Familiers, Prix, Historique, Suivis).
- **Pagination "Afficher plus"** (Prix) : affiche 5 résultats puis +5 par clic ; réinitialisé au changement de catégorie ou de recherche.
- **Onglets segmentés** (Prix catégories, Historique catégories, Suivis statuts) : vrai composant de navigation (pas des boutons isolés), état actif visuellement distinct (fond vert plein).
- **Historique** : changer de catégorie ne perd pas le contexte de la page (juste la sélection d'objet se réinitialise sur le premier objet de la nouvelle catégorie).
- **Serveurs** : renommer bascule une card en mode édition inline (input + Valider) ; activer un contexte désactive les autres (un seul actif à la fois) ; supprimer retire immédiatement la card + toast.
- **Réglages** : slider met à jour la valeur affichée en temps réel (`onChange`).

## State Management (état nécessaire côté implémentation)
- `screen` (écran actif, string parmi les 9 clés).
- `cmdkOpen`, `cmdkQuery` (état de la palette de commande).
- `trackOpen`, `trackItem`, `trackQty`, `trackPrice` (modale "+ Suivre").
- `toastVisible`, `toastMessage` (confirmation visuelle, avec timeout d'auto-masquage).
- Par écran : recherche texte (`craftsSearch`, `familiersSearch`, `prixSearch`, `histSearch`, `suivisSearch`), tri (`craftsSortKey/Dir`, `incompleteSortKey/Dir`, `suivisSortKey/Dir`), filtres (`prixCategory`, `histCategory`, `suivisFilter`), pagination (`prixLimit`).
- `histSelected` (objet actuellement affiché en Historique), `manualPrice`/`manualXp` (formulaire de saisie).
- `marginSafety` (réglage numérique).
- `servers` (liste de contextes/serveurs), `editingServerIndex`, `editServerValue`, `newServerName`.
- **Données à brancher côté backend** : toutes les données affichées dans ce prototype (`CRAFTS_RAW`, `FAMILIERS_RAW`, `PRIX_DATA`, `SUIVIS_RAW`, `INCOMPLETE_RAW`, historique par objet, etc. — voir le `<script>` du fichier HTML) sont des tableaux JS statiques de démonstration à remplacer par des appels API/DB réels (FastAPI). La logique de tri/filtre/pagination peut rester côté client si les volumes restent raisonnables, ou être déplacée côté serveur si besoin.

## Design Tokens

**Couleurs**
- Fond extérieur (page) : `#140f09`
- Fond coquille app (avec grille) : `#241a10`, lignes de grille `#34261a`, taille de grille 24px
- Sidebar : fond `#14100a`, bordure `#090604`, item actif fond `#3a2a12`
- Cartes (surface principale du contenu) : fond `#f7f0e2`, bordure `#c9b48f`
- Onglets segmentés (fond du groupe) : `#ecdfc2`
- Texte primaire sur carte : `#1c150c` / `#241b0f` (titres)
- Texte muté sur carte : `#8a7a5c` / `#b3a184` (plus clair)
- Texte clair sur fond sombre (hors carte) : `#f0e6d2`
- **Vert — profit / positif** : `#2e7d32` (bénéfice, marge %, ROI, boutons d'action primaires, badge "Vendu", nav active dot)
- **Or — argent / montants kamas** : `#b8791a` (coût, prix, payé/vente, cours médian/moyen) ; variante liens : même teinte
- **Rouge — négatif / blocage** : `#a3271a` (ressource manquante, prix non référencé, suppression) sur fond `#f0d6cf`
- **Or vif (accent nav)** : `#f0b429` (item de nav actif, dot du watcher)
- **Teal — neutre/en attente** : `#1f5a66` sur fond `#cfe3e8` (badge "En cours")
- Bouton texte clair : `#fbf3e2`

**Typographie**
- Police UI : **Archivo** (400/500/600/700/800)
- Police chiffres/mono : **Space Mono** (400/700) — utilisée pour tous les montants, labels de raccourci clavier, timestamps
- Tailles clés : titres de section carte 13–14px/800 ; KPI principaux 44px/800 (Dashboard), 24–26px/800 (autres écrans) ; corps de tableau 12.5–13px ; labels d'en-tête 10–10.5px uppercase, letter-spacing .05–.06em

**Espacement / structure**
- Rayon de bordure standard : 4px (cartes/inputs), 3px (badges/boutons compacts), 6–8px (onglets, modales, coquille)
- Gap standard entre blocs : 14–20px ; padding carte : 14–18px

**Ombres**
- Coquille app : `0 24px 60px rgba(0,0,0,.45)`
- Modales : `0 30px 70px rgba(0,0,0,.5)`
- Toast : `0 12px 30px rgba(0,0,0,.4)`

## Assets
- Aucune image/icône externe — uniquement des caractères Unicode comme glyphes (⌘, ⚠, ⇄, ▲) et un composant `<image-slot>` (placeholder de dépôt d'image, fichier `image-slot.js` inclus) pour l'écran Import.
- Polices chargées via Google Fonts (Archivo, Space Mono) — à héberger localement ou garder en CDN selon la politique du projet.

## Files
- `Kamas.dc.html` — fichier unique contenant tout le prototype (markup + logique d'état). Toute la navigation entre écrans, le tri, les filtres, la pagination, les modales et le toast y sont déjà fonctionnels en JS (à réimplémenter dans l'environnement cible).
- `image-slot.js` — composant de démonstration pour la zone de dépôt d'image (écran Import), à remplacer par l'implémentation réelle du projet.
