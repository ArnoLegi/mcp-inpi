# MCP Entreprises FR — Outils juridiques (Recherche d'Entreprises · BODACC · Marques INPI)

Serveur **MCP** (Model Context Protocol) en Python exposant des outils juridiques sur les
entreprises françaises, basés sur des API **gratuites**. Deux transports :
**Streamable HTTP** (`/mcp`, recommandé pour Claude.ai) et **SSE** (`/sse`, legacy).
Déployable directement sur **Railway** et connectable à **Claude.ai**.

## Outils exposés

| Outil | Description | Source | Auth |
|---|---|---|---|
| `rechercher_societe(denomination)` | Recherche d'entreprises par nom → SIREN, forme juridique, commune | Recherche d'Entreprises | aucune |
| `fiche_societe(siren)` | Identité : dénomination, sigle, forme juridique, activité (NAF), siège, SIREN | Recherche d'Entreprises | aucune |
| `dirigeants(siren)` | Mandataires sociaux (qualité, identité, date de naissance) | Recherche d'Entreprises | aucune |
| `statut_entreprise(siren)` | Active / cessée (état administratif INSEE) | Recherche d'Entreprises | aucune |
| `procedures_collectives(siren)` | Sauvegarde, redressement, liquidation judiciaire | BODACC | aucune |
| `portfolio_marques(siren)` | Marques déposées par une société (par SIREN) | API PI INPI | INPI* |
| `detail_marque(identifiant)` | Détail d'une marque (classes Nice, dates, statut, logo) | API PI INPI | INPI* |

\* Seuls les outils de marques nécessitent des identifiants INPI (voir Configuration).
Les bénéficiaires effectifs (UBO) ne sont pas exposés : aucune source gratuite ne les fournit.

## Sources de données

- **Recherche d'Entreprises** — `recherche-entreprises.api.gouv.fr` (data.gouv.fr / DINUM),
  basée sur SIRENE + RNE. Open data **sans clé**, réponse < 1 s. Endpoint `GET /search?q=`.
- **BODACC** — `bodacc-datadila.opendatasoft.com` (Opendatasoft v2.1), open data **sans clé**.
- **API PI Marques** — `api-gateway.inpi.fr/services/apidiffusion` — auth XSRF + cookies (INPI).

## Configuration

Les outils entreprises et BODACC fonctionnent **sans aucune clé**. Seuls les outils de
marques nécessitent des identifiants INPI. Copiez `.env.example` en `.env` (non commité) :

```env
# Requis UNIQUEMENT pour portfolio_marques / detail_marque (compte data.inpi.fr) :
INPI_USERNAME=votre_email@example.com
INPI_PASSWORD=votre_mot_de_passe

# Optionnel — compte technique API PI Marques (api-gateway.inpi.fr).
# Si absent, INPI_USERNAME/INPI_PASSWORD sont réutilisés.
# INPI_PI_USERNAME=compte_technique@example.com
# INPI_PI_PASSWORD=mot_de_passe_technique
```

> **Compte INPI (marques uniquement)** : créez-le sur https://data.inpi.fr/login, puis
> activez « Accès APIs PI » dans *Mes accès API / SFTP*. L'activation génère un **compte
> technique** (email + mot de passe propres) à mettre dans `INPI_PI_USERNAME/INPI_PI_PASSWORD`.

## Lancer en local

```bash
python -m venv .venv
.venv\Scripts\activate          # Windows (PowerShell : .venv\Scripts\Activate.ps1)
pip install -r requirements.txt
copy .env.example .env          # puis éditez .env
python main.py
```

- Endpoint Streamable HTTP : `http://localhost:8080/mcp`
- Endpoint SSE (legacy) : `http://localhost:8080/sse`
- Santé : `http://localhost:8080/health`

## Déploiement sur Railway

Les fichiers `railway.json`, `Procfile` et `.python-version` sont fournis (build Nixpacks,
Python 3.11). Railway injecte automatiquement la variable `PORT`, déjà prise en charge.

**Depuis l'interface Railway :**

1. *New Project → Deploy from GitHub repo* et sélectionnez ce dépôt.
2. (Optionnel, pour les marques) dans **Variables**, ajoutez `INPI_USERNAME`,
   `INPI_PASSWORD`. **Ne commitez jamais `.env`.** Les autres outils marchent sans clé.
3. Dans **Settings → Networking**, cliquez *Generate Domain* pour obtenir une URL publique.
4. Le déploiement se lance automatiquement ; le healthcheck pointe sur `/health`.
5. Vos endpoints publics : `https://<votre-projet>.up.railway.app/mcp` (recommandé)
   ou `.../sse` (legacy).

**Ou via la CLI Railway :**

```bash
npm i -g @railway/cli
railway login
railway init
railway up
railway domain        # génère l'URL publique
# (optionnel, marques) : railway variables --set INPI_USERNAME=... --set INPI_PASSWORD=...
```

## Connexion à Claude.ai

Les endpoints MCP sont **publics** (pas d'authentification). Dans Claude.ai →
*Settings → Connectors → Add custom connector*, renseignez l'URL **Streamable HTTP**
(recommandée) :

```
https://<votre-projet>.up.railway.app/mcp
```

> ⚠️ Utilisez bien `/mcp` (Streamable HTTP), **pas** `/sse`, avec Claude.ai. Le transport
> SSE legacy peut provoquer l'erreur « Session terminated » (code 32600) côté Claude.ai.

> ⚠️ **Sécurité** : sans authentification, toute personne connaissant l'URL peut utiliser
> le serveur. Gardez l'URL privée, ou réintroduisez une protection (reverse-proxy, IP
> allowlist, ou une couche d'auth) si nécessaire.

## Notes & limites

- **Identité** : l'API Recherche d'Entreprises ne fournit ni le **capital social** ni
  l'**objet social** (données SIRENE/RNE ouvertes). Le reste (dénomination, forme juridique,
  activité NAF, siège, dirigeants, statut) est complet et rapide (< 1 s).
- **Bénéficiaires effectifs (UBO)** : non exposés (aucune source gratuite). Nécessiteraient
  une API payante (ex. Pappers) ou l'API formalités RNE de l'INPI (habilitation requise).
- **Statut** : `active` / `cessée` selon l'état administratif INSEE (`A` / `C`).
- **Procédures collectives** : `en_cours_estimation` est une heuristique (une clôture récente
  éteint la procédure) — à confirmer auprès du greffe/tribunal compétent.
- **Marques par SIREN** : ne couvre que les marques **FR** (le SIREN n'est rattaché qu'au
  déposant / dernier titulaire ayant renouvelé). `detail_marque` renvoie la notice telle
  quelle (plus l'URL du logo).

## Structure du projet

```
mcp-inpi/
├── main.py                 # entrée : app Starlette (/mcp + /sse + /health), uvicorn
├── railway.json            # config de déploiement Railway (Nixpacks)
├── Procfile                # commande de démarrage
├── .python-version         # version Python (3.11)
├── .env.example            # modèle de configuration (secrets)
├── requirements.txt
└── inpi_mcp/
    ├── server.py                # FastMCP + définition des 7 outils
    ├── config.py                # variables d'environnement
    ├── reference.py             # formes juridiques (codes INSEE) + normalisation SIREN
    ├── entreprises_parsers.py   # extraction API Recherche d'Entreprises
    ├── parsers.py               # extraction BODACC / marques
    └── clients/
        ├── recherche_entreprises.py  # API data.gouv.fr (sans clé)
        ├── bodacc.py                 # API BODACC Opendatasoft (sans clé)
        └── marques.py                # API PI Marques INPI (XSRF + cookies)
```
