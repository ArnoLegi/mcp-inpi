"""Tables de référence et utilitaires de normalisation.

Les codes INPI/INSEE (forme juridique, rôle de dirigeant) sont conservés bruts dans
les réponses ; ces tables fournissent un libellé lisible *en plus* du code, sans
jamais l'écraser. Les mappings ne sont volontairement pas exhaustifs : en cas de
code inconnu, on renvoie le code tel quel.
"""
from __future__ import annotations

import re

# --- Formes juridiques (nomenclature INSEE des catégories juridiques, niveau III) ---
# Sous-ensemble des codes les plus fréquents. Référence complète :
# https://www.insee.fr/fr/information/2028129
FORMES_JURIDIQUES: dict[str, str] = {
    "1000": "Entrepreneur individuel",
    "5202": "Société en nom collectif (SNC)",
    "5306": "Société en commandite simple",
    "5308": "Société en commandite par actions",
    "5385": "Société d'exercice libéral en commandite par actions",
    "5410": "SARL (Société à responsabilité limitée)",
    "5415": "SARL d'économie mixte",
    "5426": "SARL immobilière de gestion",
    "5430": "SARL d'exercice libéral (SELARL)",
    "5499": "Société à responsabilité limitée (sans autre indication)",
    "5505": "SA à participation ouvrière à conseil d'administration",
    "5510": "SA à conseil d'administration",
    "5515": "SA d'économie mixte à conseil d'administration",
    "5560": "SA à directoire",
    "5599": "Société anonyme (sans autre indication)",
    "5710": "SAS (Société par actions simplifiée)",
    "5720": "SASU (SAS à associé unique)",
    "5785": "Société d'exercice libéral par actions simplifiée (SELAS)",
    "5800": "Société européenne",
    "6316": "Société coopérative agricole",
    "6411": "Société d'assurance mutuelle",
    "6533": "Société civile de moyens (SCM)",
    "6540": "Société civile immobilière (SCI)",
    "6541": "Société civile immobilière de construction-vente",
    "6599": "Société civile (sans autre indication)",
    "9220": "Association déclarée",
}

# --- Rôles / qualités de mandataires (nomenclature INPI) ---
# Sous-ensemble courant. Code conservé brut dans tous les cas.
ROLES_DIRIGEANTS: dict[str, str] = {
    "5": "Président",
    "30": "Gérant",
    "51": "Directeur général",
    "53": "Directeur général délégué",
    "60": "Membre du directoire",
    "61": "Président du directoire",
    "65": "Membre du conseil de surveillance",
    "66": "Président du conseil de surveillance",
    "67": "Administrateur",
    "70": "Commissaire aux comptes titulaire",
    "71": "Commissaire aux comptes suppléant",
    "73": "Associé indéfiniment responsable",
}


def libelle_forme_juridique(code: str | None) -> str | None:
    if not code:
        return None
    return FORMES_JURIDIQUES.get(str(code))


def libelle_role(code: str | None) -> str | None:
    if code is None:
        return None
    return ROLES_DIRIGEANTS.get(str(code))


_SIREN_RE = re.compile(r"\D")


def normaliser_siren(siren: str) -> str:
    """Nettoie et valide un SIREN. Lève ValueError si invalide.

    Accepte les SIREN avec espaces/tirets ainsi que les SIRET 14 chiffres
    (dont on extrait le SIREN, les 9 premiers chiffres).
    """
    if siren is None:
        raise ValueError("SIREN manquant")
    digits = _SIREN_RE.sub("", str(siren))
    if len(digits) == 14:  # SIRET -> SIREN
        digits = digits[:9]
    if len(digits) != 9:
        raise ValueError(
            f"SIREN invalide : '{siren}' (attendu 9 chiffres, obtenu {len(digits)})."
        )
    return digits
