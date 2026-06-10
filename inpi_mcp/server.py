"""Serveur MCP « INPI Juridique » — définition des outils exposés.

Transports : Streamable HTTP (/mcp) et SSE (/sse). Les outils interrogeant l'INPI
émettent des notifications de progression régulières (keepalive) pendant l'appel
réseau, afin que le client (Claude.ai) reçoive des octets et ne coupe pas la session
sur un timeout d'inactivité quand l'API INPI est lente.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Awaitable, TypeVar

import httpx
from mcp.server.fastmcp import Context, FastMCP

from . import entreprises_parsers, parsers
from .clients.bodacc import BodaccClient, BodaccError
from .clients.marques import MarquesClient, MarquesError, MarquesNotFound
from .clients.recherche_entreprises import (
    EntrepriseNotFound,
    RechercheEntreprisesClient,
    RechercheEntreprisesError,
)
from .clients.rne import RNEClient, RNEError
from .config import settings
from .reference import normaliser_siren

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
log = logging.getLogger("inpi_mcp.server")

mcp = FastMCP(
    "inpi-juridique",
    host=settings.host,
    port=settings.port,
    # Mode sans état : chaque requête HTTP est autonome (pas de session persistée
    # entre requêtes), ce qui simplifie le déploiement derrière un proxy/scale-out.
    stateless_http=True,
    instructions=(
        "Outils juridiques sur les entreprises françaises : identité/dirigeants/statut via "
        "l'API Recherche d'Entreprises (data.gouv.fr), procédures collectives via BODACC, "
        "marques via l'API PI de l'INPI. Les outils prennent un SIREN (9 chiffres) sauf "
        "detail_marque."
    ),
)

# --- Clients partagés (créés à la demande, réutilisés entre appels) ---------- #
_entreprises: RechercheEntreprisesClient | None = None
_bodacc: BodaccClient | None = None
_marques: MarquesClient | None = None
_rne: RNEClient | None = None


def get_entreprises() -> RechercheEntreprisesClient:
    global _entreprises
    if _entreprises is None:
        _entreprises = RechercheEntreprisesClient()
    return _entreprises


def get_rne() -> RNEClient:
    global _rne
    if _rne is None:
        _rne = RNEClient(settings.inpi_username, settings.inpi_password)
    return _rne


def get_bodacc() -> BodaccClient:
    global _bodacc
    if _bodacc is None:
        _bodacc = BodaccClient()
    return _bodacc


def get_marques() -> MarquesClient:
    global _marques
    if _marques is None:
        _marques = MarquesClient(settings.pi_username, settings.pi_password)
    return _marques


def _err(message: str, **extra) -> dict:
    return {"erreur": message, **extra}


def _valider_siren(siren: str) -> str:
    return normaliser_siren(siren)


T = TypeVar("T")

# Intervalle entre deux notifications de keepalive pendant un appel réseau (secondes).
_KEEPALIVE_INTERVAL = 3.0


async def _avec_keepalive(ctx: Context | None, coro: Awaitable[T], label: str) -> T:
    """Exécute `coro` en émettant des notifications régulières pour garder le flux actif.

    Tant que l'appel réseau (INPI/BODACC) n'est pas terminé, on envoie périodiquement une
    notification `info` au client : il reçoit ainsi des octets et ne considère pas la
    session comme morte (évite « Session terminated » sur appel lent). Les exceptions de
    `coro` sont propagées telles quelles.
    """
    task: asyncio.Task[T] = asyncio.ensure_future(coro)
    try:
        if ctx is not None:
            try:
                await ctx.info(f"{label}…")
            except Exception:  # noqa: BLE001 — le keepalive ne doit jamais faire échouer l'outil
                pass
        secondes = 0
        while True:
            done, _ = await asyncio.wait({task}, timeout=_KEEPALIVE_INTERVAL)
            if done:
                return task.result()
            secondes += int(_KEEPALIVE_INTERVAL)
            if ctx is not None:
                try:
                    await ctx.info(f"{label} toujours en cours ({secondes}s)…")
                except Exception:  # noqa: BLE001
                    pass
    except httpx.TimeoutException as e:
        # str(ReadTimeout) est souvent vide : on renvoie un message explicite.
        raise TimeoutError(
            f"Délai dépassé ({label}) : l'API INPI/BODACC est trop lente. Réessayez."
        ) from e
    finally:
        if not task.done():
            task.cancel()


# --------------------------------------------------------------------------- #
# Outils entreprises (API Recherche d'Entreprises — data.gouv.fr)
# --------------------------------------------------------------------------- #

# Délai max pour l'enrichissement RNE : il ne doit jamais retarder/bloquer la fiche.
_RNE_TIMEOUT = 8.0


async def _complement_rne(siren: str) -> dict:
    """Enrichissement optionnel via l'API RNE/INPI (capital, objet, greffe RCS).

    Tolérant à l'échec : tout problème (identifiants absents, timeout, 4xx/5xx, SIREN
    inconnu au RNE) est avalé et renvoie `{}` — la fiche est alors servie sans ces champs.
    """
    if not settings.has_inpi_credentials:
        return {}
    try:
        formality = await asyncio.wait_for(get_rne().get_company(siren), timeout=_RNE_TIMEOUT)
        return entreprises_parsers.parse_rne_complement(formality)
    except (asyncio.TimeoutError, RNEError, httpx.HTTPError) as e:
        log.info("Enrichissement RNE ignoré pour %s : %s", siren, e)
        return {}
    except Exception as e:  # noqa: BLE001
        log.warning("Enrichissement RNE inattendu ignoré pour %s : %s", siren, e)
        return {}


@mcp.tool()
async def fiche_societe(siren: str, ctx: Context) -> dict:
    """Identité d'une société française (data.gouv.fr, enrichie si possible par le RNE/INPI).

    Renvoie dénomination, sigle, forme juridique, activité principale (NAF), date de
    création, adresse du siège et SIREN (source : API Recherche d'Entreprises). Quand
    l'API RNE/INPI répond à temps, la fiche est enrichie du capital social, de l'objet
    social et du greffe RCS ; sinon elle est renvoyée sans ces champs.

    Args:
        siren: Numéro SIREN (9 chiffres ; espaces/tirets et SIRET 14 chiffres tolérés).
    """
    try:
        siren = _valider_siren(siren)
    except ValueError as e:
        return _err(str(e))
    # Enrichissement RNE lancé en parallèle de la requête data.gouv.fr (borné à 8s).
    rne_task = asyncio.ensure_future(_complement_rne(siren))
    try:
        result = await _avec_keepalive(
            ctx, get_entreprises().par_siren(siren), f"Recherche entreprise {siren}"
        )
    except (EntrepriseNotFound, RechercheEntreprisesError) as e:
        rne_task.cancel()
        return _err(str(e), siren=siren)
    except Exception as e:  # noqa: BLE001
        rne_task.cancel()
        log.exception("fiche_societe")
        return _err(f"Erreur inattendue : {e}", siren=siren)

    fiche = entreprises_parsers.parse_fiche(result)
    complement = await rne_task  # ne lève jamais (échecs avalés -> {})
    if complement:
        fiche.update(complement)
        fiche["source"] = "Recherche d'Entreprises (data.gouv.fr) + RNE (INPI)"
        fiche.pop("note", None)
    return fiche


@mcp.tool()
async def rechercher_societe(
    denomination: str, ctx: Context, page: int = 1, page_size: int = 20
) -> dict:
    """Recherche d'entreprises par dénomination sociale (source : API Recherche d'Entreprises).

    Utile pour retrouver le SIREN d'une société à partir de son nom, avant d'appeler
    les autres outils. Renvoie une liste condensée (SIREN, dénomination, forme
    juridique, commune, statut).

    Args:
        denomination: Nom (ou partie du nom) de la société recherchée.
        page: Numéro de page (défaut 1).
        page_size: Nombre de résultats par page, 1 à 25 (défaut 20).
    """
    terme = (denomination or "").strip()
    if len(terme) < 2:
        return _err("Dénomination trop courte (au moins 2 caractères).")
    try:
        data = await _avec_keepalive(
            ctx,
            get_entreprises().search(terme, page=page, per_page=page_size),
            f"Recherche « {terme} »",
        )
        res = entreprises_parsers.parse_resultats(data)
        return {
            "recherche": terme,
            "page": res["page"],
            "total": res["total"],
            "nombre": len(res["societes"]),
            "societes": res["societes"],
        }
    except RechercheEntreprisesError as e:
        return _err(str(e), recherche=terme)
    except Exception as e:  # noqa: BLE001
        log.exception("rechercher_societe")
        return _err(f"Erreur inattendue : {e}", recherche=terme)


@mcp.tool()
async def dirigeants(siren: str, ctx: Context) -> dict:
    """Liste des dirigeants / mandataires sociaux d'une société (source : Recherche d'Entreprises).

    Pour chaque mandataire : qualité, nom/prénoms (ou dénomination + SIREN si personne
    morale), date de naissance et nationalité quand disponibles.

    Args:
        siren: Numéro SIREN (9 chiffres).
    """
    try:
        siren = _valider_siren(siren)
    except ValueError as e:
        return _err(str(e))
    try:
        result = await _avec_keepalive(
            ctx, get_entreprises().par_siren(siren), f"Recherche entreprise {siren}"
        )
        liste = entreprises_parsers.parse_dirigeants(result)
        return {"siren": siren, "nombre": len(liste), "dirigeants": liste}
    except (EntrepriseNotFound, RechercheEntreprisesError) as e:
        return _err(str(e), siren=siren)
    except Exception as e:  # noqa: BLE001
        log.exception("dirigeants")
        return _err(f"Erreur inattendue : {e}", siren=siren)


@mcp.tool()
async def statut_entreprise(siren: str, ctx: Context) -> dict:
    """Statut d'activité d'une société : active ou cessée (source : Recherche d'Entreprises).

    Déduit de l'état administratif INSEE (A = active, C = cessée).

    Args:
        siren: Numéro SIREN (9 chiffres).
    """
    try:
        siren = _valider_siren(siren)
    except ValueError as e:
        return _err(str(e))
    try:
        result = await _avec_keepalive(
            ctx, get_entreprises().par_siren(siren), f"Recherche entreprise {siren}"
        )
        return entreprises_parsers.parse_statut(result)
    except (EntrepriseNotFound, RechercheEntreprisesError) as e:
        return _err(str(e), siren=siren)
    except Exception as e:  # noqa: BLE001
        log.exception("statut_entreprise")
        return _err(f"Erreur inattendue : {e}", siren=siren)


# --------------------------------------------------------------------------- #
# Outil BODACC
# --------------------------------------------------------------------------- #

@mcp.tool()
async def procedures_collectives(siren: str, ctx: Context) -> dict:
    """Procédures collectives d'une société via le BODACC (sauvegarde, RJ, liquidation).

    Recherche les annonces BODACC de famille « procédures collectives » pour le SIREN
    et les classe (sauvegarde / redressement / liquidation / plan / clôture). Source
    open data, sans authentification.

    Args:
        siren: Numéro SIREN (9 chiffres).
    """
    try:
        siren = _valider_siren(siren)
    except ValueError as e:
        return _err(str(e))
    try:
        records = await _avec_keepalive(
            ctx,
            get_bodacc().annonces_par_siren(siren, famille="collective"),
            f"Interrogation BODACC {siren}",
        )
        result = parsers.parse_procedures(records)
        result["siren"] = siren
        return result
    except BodaccError as e:
        return _err(str(e), siren=siren)
    except Exception as e:  # noqa: BLE001
        log.exception("procedures_collectives")
        return _err(f"Erreur inattendue : {e}", siren=siren)


# --------------------------------------------------------------------------- #
# Outils Marques (API PI)
# --------------------------------------------------------------------------- #

@mcp.tool()
async def portfolio_marques(
    siren: str, ctx: Context, collections: list[str] | None = None
) -> dict:
    """Portefeuille de marques déposées par une société, par SIREN (source : API PI/INPI).

    Le SIREN n'est rattaché qu'aux marques françaises (FR) ; les marques EU/WO n'ont pas
    de SIREN et peuvent manquer. Pour les marques anciennes/cédées, une recherche
    complémentaire par nom de déposant peut être nécessaire.

    Args:
        siren: Numéro SIREN (9 chiffres) du titulaire/déposant.
        collections: Collections à interroger, parmi ["FR","EU","WO"]. Défaut : ["FR"].
    """
    try:
        siren = _valider_siren(siren)
    except ValueError as e:
        return _err(str(e))
    try:
        hits = await _avec_keepalive(
            ctx,
            get_marques().search_par_siren(siren, collections=collections),
            f"Recherche marques {siren}",
        )
        marques = [parsers.parse_marque_hit(h) for h in hits]
        return {
            "siren": siren,
            "collections": collections or ["FR"],
            "nombre": len(marques),
            "marques": marques,
        }
    except MarquesError as e:
        return _err(str(e), siren=siren)
    except Exception as e:  # noqa: BLE001
        log.exception("portfolio_marques")
        return _err(f"Erreur inattendue : {e}", siren=siren)


@mcp.tool()
async def detail_marque(identifiant: str, ctx: Context) -> dict:
    """Détail d'une marque par son identifiant (collection + numéro, ex. 'FR4216963').

    Renvoie la notice complète (dénomination, classes de Nice, dates, statut, titulaire)
    et l'URL du logo/visuel. Source : API PI/INPI.

    Args:
        identifiant: Identifiant marque = collection + numéro national, ex. 'FR4216963'.
                     Un numéro seul (ex. '4216963') est préfixé 'FR' par défaut.
    """
    ident = (identifiant or "").strip().upper().replace(" ", "")
    if not ident:
        return _err("Identifiant de marque manquant.")
    if ident.isdigit():
        ident = f"FR{ident}"
    try:
        xml = await _avec_keepalive(ctx, get_marques().notice(ident), f"Notice marque {ident}")
        return {
            "identifiant": ident,
            "logo_url": get_marques().image_url(ident),
            "notice": parsers.parse_marque_notice(xml),
        }
    except MarquesNotFound as e:
        return _err(str(e), identifiant=ident)
    except MarquesError as e:
        return _err(str(e), identifiant=ident)
    except Exception as e:  # noqa: BLE001
        log.exception("detail_marque")
        return _err(f"Erreur inattendue : {e}", identifiant=ident)
