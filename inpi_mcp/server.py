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

from mcp.server.fastmcp import Context, FastMCP

from . import parsers
from .clients.bodacc import BodaccClient, BodaccError
from .clients.marques import MarquesClient, MarquesError, MarquesNotFound
from .clients.rne import RNEClient, RNEError, RNENotFound
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
        "Outils juridiques sur les entreprises françaises via les API gratuites de l'INPI "
        "(RNE pour l'identité/dirigeants/UBO, BODACC pour les procédures collectives, "
        "API PI pour les marques). Les outils prennent un SIREN (9 chiffres) sauf detail_marque."
    ),
)

# --- Clients partagés (créés à la demande, réutilisés entre appels) ---------- #
_rne: RNEClient | None = None
_bodacc: BodaccClient | None = None
_marques: MarquesClient | None = None


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
    finally:
        if not task.done():
            task.cancel()


# --------------------------------------------------------------------------- #
# Outils RNE
# --------------------------------------------------------------------------- #

@mcp.tool()
async def fiche_societe(siren: str, ctx: Context) -> dict:
    """Identité complète d'une société française (source : RNE/INPI).

    Renvoie dénomination, sigle, forme juridique, objet social, capital social,
    adresse du siège et SIREN.

    Args:
        siren: Numéro SIREN (9 chiffres ; espaces/tirets et SIRET 14 chiffres tolérés).
    """
    try:
        siren = _valider_siren(siren)
    except ValueError as e:
        return _err(str(e))
    try:
        data = await _avec_keepalive(ctx, get_rne().get_company(siren), f"Interrogation RNE {siren}")
        return parsers.parse_fiche(data)
    except RNENotFound as e:
        return _err(str(e), siren=siren)
    except RNEError as e:
        return _err(str(e), siren=siren)
    except Exception as e:  # noqa: BLE001
        log.exception("fiche_societe")
        return _err(f"Erreur inattendue : {e}", siren=siren)


@mcp.tool()
async def rechercher_societe(
    denomination: str, ctx: Context, page: int = 1, page_size: int = 20
) -> dict:
    """Recherche d'entreprises par dénomination sociale (source : RNE/INPI).

    Utile pour retrouver le SIREN d'une société à partir de son nom, avant d'appeler
    les autres outils. Renvoie une liste condensée (SIREN, dénomination, forme
    juridique, commune).

    Args:
        denomination: Nom (ou partie du nom) de la société recherchée.
        page: Numéro de page (défaut 1).
        page_size: Nombre de résultats par page, 1 à 100 (défaut 20).
    """
    terme = (denomination or "").strip()
    if len(terme) < 2:
        return _err("Dénomination trop courte (au moins 2 caractères).")
    try:
        results = await _avec_keepalive(
            ctx,
            get_rne().search_companies(terme, page_size=page_size, page=page),
            f"Recherche RNE « {terme} »",
        )
        societes = [parsers.parse_resultat_recherche(r) for r in results]
        return {
            "recherche": terme,
            "page": page,
            "nombre": len(societes),
            "societes": societes,
        }
    except RNEError as e:
        return _err(str(e), recherche=terme)
    except Exception as e:  # noqa: BLE001
        log.exception("rechercher_societe")
        return _err(f"Erreur inattendue : {e}", recherche=terme)


@mcp.tool()
async def dirigeants(siren: str, ctx: Context) -> dict:
    """Liste des dirigeants / mandataires sociaux d'une société (source : RNE/INPI).

    Pour chaque mandataire : qualité (code + libellé), nom/prénoms ou dénomination si
    personne morale, et indicateur de bénéficiaire effectif.

    Args:
        siren: Numéro SIREN (9 chiffres).
    """
    try:
        siren = _valider_siren(siren)
    except ValueError as e:
        return _err(str(e))
    try:
        data = await _avec_keepalive(ctx, get_rne().get_company(siren), f"Interrogation RNE {siren}")
        liste = parsers.parse_dirigeants(data)
        return {"siren": siren, "nombre": len(liste), "dirigeants": liste}
    except (RNENotFound, RNEError) as e:
        return _err(str(e), siren=siren)
    except Exception as e:  # noqa: BLE001
        log.exception("dirigeants")
        return _err(f"Erreur inattendue : {e}", siren=siren)


@mcp.tool()
async def beneficiaires_effectifs(siren: str, ctx: Context) -> dict:
    """Bénéficiaires effectifs (UBO) déclarés d'une société (source : RNE/INPI).

    Renvoie l'identité des UBO et, si disponibles, leurs modalités de contrôle
    (part de capital / droits de vote). Note : ces données sont parfois restreintes
    par l'INPI (accès 403) ou non déclarées.

    Args:
        siren: Numéro SIREN (9 chiffres).
    """
    try:
        siren = _valider_siren(siren)
    except ValueError as e:
        return _err(str(e))
    try:
        data = await _avec_keepalive(ctx, get_rne().get_company(siren), f"Interrogation RNE {siren}")
        liste = parsers.parse_beneficiaires(data)
        return {
            "siren": siren,
            "nombre": len(liste),
            "beneficiaires_effectifs": liste,
            "note": None if liste else "Aucun bénéficiaire effectif déclaré ou accessible.",
        }
    except (RNENotFound, RNEError) as e:
        return _err(str(e), siren=siren)
    except Exception as e:  # noqa: BLE001
        log.exception("beneficiaires_effectifs")
        return _err(f"Erreur inattendue : {e}", siren=siren)


@mcp.tool()
async def statut_entreprise(siren: str, ctx: Context) -> dict:
    """Statut d'activité d'une société : actif, radié, en cessation/liquidation (RNE/INPI).

    Le statut est déduit des événements de l'historique RNE et des blocs de cessation.

    Args:
        siren: Numéro SIREN (9 chiffres).
    """
    try:
        siren = _valider_siren(siren)
    except ValueError as e:
        return _err(str(e))
    try:
        data = await _avec_keepalive(ctx, get_rne().get_company(siren), f"Interrogation RNE {siren}")
        return parsers.parse_statut(data)
    except (RNENotFound, RNEError) as e:
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
        notice = await _avec_keepalive(ctx, get_marques().notice(ident), f"Notice marque {ident}")
        return {
            "identifiant": ident,
            "logo_url": get_marques().image_url(ident),
            "notice": notice,
        }
    except MarquesNotFound as e:
        return _err(str(e), identifiant=ident)
    except MarquesError as e:
        return _err(str(e), identifiant=ident)
    except Exception as e:  # noqa: BLE001
        log.exception("detail_marque")
        return _err(f"Erreur inattendue : {e}", identifiant=ident)
