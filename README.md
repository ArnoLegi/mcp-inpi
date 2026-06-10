# MCP INPI — Outils juridiques entreprises (RNE · BODACC · Marques)

Serveur **MCP** (Model Context Protocol) en Python exposant des outils juridiques sur les
entreprises françaises, basés sur trois API **gratuites** de l'INPI / DILA. Deux transports :
**Streamable HTTP** (`/mcp`, recommandé pour Claude.ai) et **SSE** (`/sse`, legacy).
Déployable directement sur **Railway** et connectable à **Claude.ai**.

## Outils exposés

| Outil | Description | Source | Auth |
|---|---|---|---|
| `rechercher_societe(denomination)` | Recherche d'entreprises par nom → SIREN, forme juridique, commune | RNE | INPI |
| `fiche_societe(siren)` | Identité : dénomination, forme juridique, siège, capital, objet social, SIREN | RNE | INPI |
| `dirigeants(siren)` | Mandataires sociaux (qualité, identité, flag UBO) | RNE | INPI |
| `beneficiaires_effectifs(siren)` | Bénéficiaires effectifs (UBO) et modalités de contrôle | RNE | INPI |
| `statut_entreprise(siren)` | Actif / radié / en cessation / liquidation | RNE | INPI |
| `procedures_collectives(siren)` | Sauvegarde, redressement, liquidation judiciaire | BODACC | aucune |
| `portfolio_marques(siren)` | Marques déposées par une société (par SIREN) | API PI | INPI* |
| `detail_marque(identifiant)` | Détail d'une marque (classes Nice, dates, statut, logo) | API PI | INPI* |

\* L'API PI Marques utilise un **compte technique distinct** (voir Configuration).

## Sources de données

- **RNE** — `registre-national-entreprises.inpi.fr/api` — login `POST /api/sso/login`
  → token Bearer ; `GET /api/companies/{siren}`. Quota : 10 000 req/jour.
- **BODACC** — `bodacc-datadila.opendatasoft.com` (Opendatasoft v2.1), open data **sans auth**.
- **API PI Marques** — `api-gateway.inpi.fr/services/apidiffusion` — auth XSRF + cookies.

## Configuration

Copiez `.env.example` en `.env` (non commité) et renseignez :

```env
INPI_USERNAME=votre_email@example.com      # compte data.inpi.fr (API RNE)
INPI_PASSWORD=votre_mot_de_passe

# Optionnel — compte technique API PI Marques (api-gateway.inpi.fr).
# Si absent, INPI_USERNAME/INPI_PASSWORD sont réutilisés.
# INPI_PI_USERNAME=compte_technique@example.com
# INPI_PI_PASSWORD=mot_de_passe_technique

# Clé Bearer protégeant l'endpoint MCP (voir « Authentification de l'endpoint MCP »).
MCP_API_KEY=mcpinpi_remplacez_par_votre_cle
```

> **Compte INPI** : créez-le sur https://data.inpi.fr/login, puis activez « Accès API RNE »
> (et « Accès APIs PI » pour les marques) dans *Mes accès API / SFTP*. L'activation de
> l'API PI génère un **compte technique** (email + mot de passe propres) à mettre dans
> `INPI_PI_USERNAME/INPI_PI_PASSWORD`.

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
2. Dans **Variables**, ajoutez `INPI_USERNAME`, `INPI_PASSWORD`
   (et éventuellement `INPI_PI_USERNAME`, `INPI_PI_PASSWORD`). **Ne commitez jamais `.env`.**
3. Dans **Settings → Networking**, cliquez *Generate Domain* pour obtenir une URL publique.
4. Le déploiement se lance automatiquement ; le healthcheck pointe sur `/health`.
5. Vos endpoints publics : `https://<votre-projet>.up.railway.app/mcp` (recommandé)
   ou `.../sse` (legacy).

**Ou via la CLI Railway :**

```bash
npm i -g @railway/cli
railway login
railway init
railway variables --set INPI_USERNAME=... --set INPI_PASSWORD=... --set MCP_API_KEY=...
railway up
railway domain        # génère l'URL publique
```

## Authentification de l'endpoint MCP

Le serveur peut être protégé par une **clé Bearer** via la variable `MCP_API_KEY` :

- Si `MCP_API_KEY` est définie, toute requête sur `/mcp`, `/sse` et `/messages/` doit
  présenter le token, **au choix** :
  - via l'en-tête `Authorization: Bearer <MCP_API_KEY>`, ou
  - via le paramètre d'URL `?token=<MCP_API_KEY>` (ex. `.../mcp?token=...`, pratique pour
    les clients ne gérant pas les en-têtes, comme OpenLégi).
  Sinon → **401**.
- `/health` reste accessible sans clé (healthcheck Railway).
- Si `MCP_API_KEY` est absente, l'endpoint est **public** (déconseillé en production ;
  un avertissement est journalisé au démarrage).

Générez une clé :

```bash
python -c "import secrets; print('mcpinpi_' + secrets.token_urlsafe(32))"
```

## Connexion à Claude.ai

Dans Claude.ai → *Settings → Connectors → Add custom connector*, renseignez l'URL
**Streamable HTTP** (recommandée) avec le token dans l'URL :

```
https://<votre-projet>.up.railway.app/mcp?token=<votre MCP_API_KEY>
```

> ⚠️ **Important** : utilisez bien `/mcp` (Streamable HTTP), **pas** `/sse`, lorsque vous
> passez le token via `?token=`. En SSE, Claude.ai ne conserve pas la query string de
> l'URL `/messages/` annoncée par le serveur, ce qui provoque l'erreur
> « Session terminated » (code 32600). Avec Streamable HTTP, toutes les requêtes vont sur
> la même URL `/mcp`, donc le token est toujours transmis.

Alternative par en-tête (fonctionne aussi bien sur `/mcp` que `/sse`) :

```
Authorization: Bearer <votre MCP_API_KEY>
```

## Notes & limites

- **UBO** : données parfois confidentielles côté INPI (réponse `403`) ou non déclarées.
- **Statut** : déduit des événements RNE et blocs de cessation (pas de champ unique « statut »).
- **Procédures collectives** : `en_cours_estimation` est une heuristique (une clôture récente
  éteint la procédure) — à confirmer auprès du greffe/tribunal compétent.
- **Marques par SIREN** : ne couvre que les marques **FR** (le SIREN n'est rattaché qu'au
  déposant / dernier titulaire ayant renouvelé). Le schéma JSON de la notice marque peut
  varier : `detail_marque` renvoie la notice telle quelle (plus l'URL du logo).
- **Forme juridique / rôles** : codes INSEE/INPI conservés bruts + libellé quand connu.

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
    ├── server.py           # FastMCP + définition des 8 outils
    ├── config.py           # variables d'environnement
    ├── reference.py        # tables (formes juridiques, rôles) + normalisation SIREN
    ├── parsers.py          # extraction RNE / BODACC / marques
    └── clients/
        ├── rne.py          # API RNE (token Bearer, refresh sur 401)
        ├── bodacc.py       # API BODACC Opendatasoft (sans auth)
        └── marques.py      # API PI Marques (XSRF + cookies)
```
