"""Extraction des données métier pour les annonces BODACC et les marques INPI.

(L'identité / dirigeants / statut proviennent de l'API Recherche d'Entreprises et sont
traités dans `entreprises_parsers.py`.) Parsing défensif via `.get`.
"""
from __future__ import annotations

# --------------------------------------------------------------------------- #
# BODACC — procédures collectives
# --------------------------------------------------------------------------- #

_PROC_TYPES = {
    "sauvegarde": ("sauvegarde",),
    "redressement_judiciaire": ("redressement",),
    "liquidation_judiciaire": ("liquidation",),
    "plan": ("plan de",),
    "cloture": ("clôture", "cloture"),
    "conversion": ("conversion",),
}


def classifier_procedure(nature: str | None) -> str | None:
    if not nature:
        return None
    low = nature.lower()
    for typ, kws in _PROC_TYPES.items():
        if any(k in low for k in kws):
            return typ
    return "autre"


def parse_procedures(records: list[dict]) -> dict:
    """Transforme les annonces BODACC `collective` en synthèse de procédures."""
    annonces: list[dict] = []
    types_actifs: set[str] = set()

    for rec in records:
        if rec.get("typeavis") not in (None, "annonce"):
            continue  # on ignore rectificatifs / annulations
        jugement = rec.get("jugement")
        nature = jugement.get("nature") if isinstance(jugement, dict) else None
        typ = classifier_procedure(nature)
        if typ:
            types_actifs.add(typ)
        annonces.append(
            {
                "type_procedure": typ,
                "nature": nature,
                "famille_jugement": jugement.get("famille")
                if isinstance(jugement, dict)
                else None,
                "date_jugement": jugement.get("date")
                if isinstance(jugement, dict)
                else None,
                "date_parution": rec.get("dateparution"),
                "tribunal": rec.get("tribunal"),
                "complement": jugement.get("complementJugement")
                if isinstance(jugement, dict)
                else None,
                "id_annonce": rec.get("id"),
            }
        )

    # Une clôture postérieure éteint la procédure ; heuristique simple.
    en_cours = bool(annonces) and "cloture" not in {
        a["type_procedure"] for a in annonces[:1]  # annonce la plus récente
    }

    return {
        "procedure_collective_detectee": bool(annonces),
        "en_cours_estimation": en_cours,
        "types_rencontres": sorted(types_actifs) or None,
        "nombre_annonces": len(annonces),
        "annonces": annonces,
        "avertissement": (
            "Estimation basée sur les annonces BODACC publiées. "
            "Vérifiez le statut réel auprès du greffe / tribunal compétent."
        ),
    }


# --------------------------------------------------------------------------- #
# Marques (API PI)
# --------------------------------------------------------------------------- #

def _first(d: dict, *keys, default=None):
    for k in keys:
        if k in d and d[k] not in (None, ""):
            return d[k]
    return default


def parse_marque_hit(hit: dict) -> dict:
    """Synthèse d'une marque depuis un résultat de /search (champs ST.66)."""
    classes = _first(hit, "ClassNumber", "classNumber", default=[])
    if isinstance(classes, (str, int)):
        classes = [classes]
    return {
        "numero_national": _first(hit, "ApplicationNumber", "applicationNumber"),
        "identifiant": _first(hit, "ukey", "ApplicationNumber"),
        "denomination": _first(hit, "Mark", "mark"),
        "statut": _first(hit, "MarkCurrentStatusCode", "markCurrentStatusCode"),
        "date_depot": _first(hit, "ApplicationDate", "applicationDate"),
        "date_enregistrement": _first(hit, "RegistrationDate", "registrationDate"),
        "date_expiration": _first(hit, "ExpiryDate", "expiryDate"),
        "classes_nice": classes or None,
        "type_marque": _first(hit, "MarkFeature", "markFeature"),
        "deposant": _first(hit, "DEPOSANT", "deposant"),
        "titulaire": _first(hit, "DEPOTIT", "depotit"),
        "siren_titulaire": _first(hit, "ApplicantIdentifier", "applicantIdentifier"),
    }
