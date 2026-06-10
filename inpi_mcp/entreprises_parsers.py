"""Extraction des données métier depuis les réponses de l'API Recherche d'Entreprises.

Mapping établi d'après la structure réelle de l'API (results[].siren, nom_raison_sociale,
nature_juridique, siege, dirigeants, etat_administratif…). Parsing défensif via `.get`.
"""
from __future__ import annotations

import re

from .reference import libelle_forme_juridique

_ETATS = {"A": "active", "C": "cessée"}


def _statut(etat: str | None) -> str:
    return _ETATS.get(etat or "", "inconnu")


def _siege(result: dict) -> dict | None:
    siege = result.get("siege") or {}
    if not siege:
        return None
    return {
        "adresse": siege.get("adresse") or siege.get("geo_adresse"),
        "code_postal": siege.get("code_postal"),
        "commune": siege.get("libelle_commune"),
        "siret": siege.get("siret"),
    }


def parse_fiche(result: dict) -> dict:
    """Identité de la société (fiche_societe)."""
    code_fj = result.get("nature_juridique")
    return {
        "source": "Recherche d'Entreprises (data.gouv.fr)",
        "siren": result.get("siren"),
        "denomination": result.get("nom_raison_sociale") or result.get("nom_complet"),
        "nom_complet": result.get("nom_complet"),
        "sigle": result.get("sigle"),
        "forme_juridique_code": code_fj,
        "forme_juridique": libelle_forme_juridique(code_fj),
        "activite_principale_naf": result.get("activite_principale"),
        "categorie_entreprise": result.get("categorie_entreprise"),
        "tranche_effectif_salarie": result.get("tranche_effectif_salarie"),
        "date_creation": result.get("date_creation"),
        "statut": _statut(result.get("etat_administratif")),
        "siege": _siege(result),
        "note": (
            "Capital social et objet social ne sont pas fournis par cette source "
            "(données SIRENE/RNE ouvertes)."
        ),
    }


# --------------------------------------------------------------------------- #
# Enrichissement RNE (API INPI) — capital, objet social, greffe RCS
# --------------------------------------------------------------------------- #

# « RCS DIJON », « RCS DIJON (2104) », « R.C.S. PARIS » → capture la ville du greffe.
_RCS_RE = re.compile(r"R\.?C\.?S\.?\s+([A-ZÀ-ÖØ-Þ][A-ZÀ-ÖØ-Þ'\-]+(?:\s+[A-ZÀ-ÖØ-Þ'\-]+){0,2})")


def _greffe_depuis_observations(personne_morale: dict) -> str | None:
    """Best-effort : extrait la ville du greffe RCS du texte des observations RCS.

    Le RNE n'expose pas de champ greffe structuré ; l'info n'apparaît que dans le
    texte libre des observations (« ... RCS DIJON (2104) ... »). On prend la dernière
    observation (la plus récente) qui mentionne un greffe.
    """
    observations = (personne_morale.get("observations") or {}).get("rcs") or []
    for obs in reversed(observations):
        texte = obs.get("texte") or ""
        m = _RCS_RE.search(texte)
        if m:
            ville = m.group(1).strip().rstrip(" -").title()
            return f"Greffe de {ville}"
    return None


def parse_rne_complement(formality: dict) -> dict:
    """Champs d'enrichissement issus de l'API RNE/INPI : capital, objet, greffe RCS.

    Renvoie uniquement les clés effectivement disponibles (toute valeur absente est
    omise) afin de fusionner proprement dans la fiche data.gouv.fr. Renvoie `{}` si la
    formalité ne contient pas de personne morale exploitable.
    """
    root = formality.get("formality") or formality
    content = root.get("content") or formality.get("content") or {}
    pm = content.get("personneMorale") or {}
    description = ((pm.get("identite") or {}).get("description")) or {}

    complement: dict = {}

    capital = description.get("montantCapital")
    if capital is not None:
        complement["capital_social"] = capital
        complement["devise_capital"] = description.get("deviseCapital") or "EUR"
        complement["capital_variable"] = description.get("capitalVariable")

    objet = description.get("objet")
    if objet:
        complement["objet_social"] = objet

    greffe = _greffe_depuis_observations(pm)
    if greffe:
        complement["greffe_rcs"] = greffe

    rncs = (content.get("registreAnterieur") or {}).get("rncs") or {}
    if rncs.get("estPresent") and rncs.get("dateImmatriculation"):
        # On ne garde que la date (l'heure/fuseau n'apporte rien ici).
        complement["date_immatriculation_rcs"] = str(rncs["dateImmatriculation"])[:10]

    return complement


def parse_dirigeants(result: dict) -> list[dict]:
    """Mandataires sociaux (dirigeants)."""
    out: list[dict] = []
    for d in result.get("dirigeants") or []:
        if d.get("type_dirigeant") == "personne morale":
            out.append(
                {
                    "type": "personne_morale",
                    "qualite": d.get("qualite"),
                    "denomination": d.get("denomination"),
                    "siren": d.get("siren"),
                }
            )
        else:
            out.append(
                {
                    "type": "personne_physique",
                    "qualite": d.get("qualite"),
                    "nom": d.get("nom"),
                    "prenoms": d.get("prenoms"),
                    "date_naissance": d.get("date_de_naissance") or d.get("annee_de_naissance"),
                    "nationalite": d.get("nationalite"),
                }
            )
    return out


def parse_statut(result: dict) -> dict:
    """Statut d'activité (statut_entreprise)."""
    etat = result.get("etat_administratif")
    return {
        "siren": result.get("siren"),
        "statut": _statut(etat),
        "etat_administratif": etat,
        "date_creation": result.get("date_creation"),
        "date_fermeture": result.get("date_fermeture"),
    }


def parse_resultats(data: dict) -> dict:
    """Liste condensée des résultats de recherche (rechercher_societe)."""
    societes = []
    for r in data.get("results") or []:
        siege = r.get("siege") or {}
        code_fj = r.get("nature_juridique")
        societes.append(
            {
                "siren": r.get("siren"),
                "denomination": r.get("nom_raison_sociale") or r.get("nom_complet"),
                "sigle": r.get("sigle"),
                "forme_juridique_code": code_fj,
                "forme_juridique": libelle_forme_juridique(code_fj),
                "commune": siege.get("libelle_commune"),
                "code_postal": siege.get("code_postal"),
                "statut": _statut(r.get("etat_administratif")),
            }
        )
    return {
        "total": data.get("total_results"),
        "page": data.get("page"),
        "societes": societes,
    }
