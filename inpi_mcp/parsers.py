"""Extraction des données métier depuis les réponses brutes des API INPI.

Les chemins de clés suivent la doc « API formalités » v4.0. Tout est défensif
(`.get` en cascade) car certains blocs sont optionnels ou confidentiels.
"""
from __future__ import annotations

from typing import Any

from .reference import libelle_forme_juridique, libelle_role

# --------------------------------------------------------------------------- #
# Accès aux blocs racine
# --------------------------------------------------------------------------- #

def _content(formality: dict) -> dict:
    """Renvoie le bloc `content`, que la racine soit {formality:{content}} ou {content}."""
    if "formality" in formality and isinstance(formality["formality"], dict):
        formality = formality["formality"]
    return formality.get("content") or {}


def _personne_morale(formality: dict) -> dict:
    return _content(formality).get("personneMorale") or {}


def _personne_physique(formality: dict) -> dict:
    return _content(formality).get("personnePhysique") or {}


def _siren(formality: dict) -> str | None:
    root = formality.get("formality", formality)
    return root.get("siren") or _content(formality).get("siren")


# --------------------------------------------------------------------------- #
# Identité (fiche société)
# --------------------------------------------------------------------------- #

def parse_fiche(formality: dict) -> dict:
    content = _content(formality)
    pm = _personne_morale(formality)
    pp = _personne_physique(formality)

    if pm:
        identite = pm.get("identite") or {}
        entreprise = identite.get("entreprise") or {}
        description = identite.get("description") or {}
        forme_code = entreprise.get("formeJuridique") or (
            content.get("natureCreation") or {}
        ).get("formeJuridique")
        adresse = ((pm.get("adresseEntreprise") or {}).get("adresse")) or {}

        return {
            "type_personne": "morale",
            "siren": _siren(formality),
            "denomination": entreprise.get("denomination"),
            "sigle": description.get("sigle"),
            "nom_commercial": entreprise.get("nomCommercial"),
            "forme_juridique_code": forme_code,
            "forme_juridique": libelle_forme_juridique(forme_code),
            "objet_social": description.get("objet"),
            "capital_social": description.get("montantCapital"),
            "devise_capital": description.get("deviseCapital") or "EUR",
            "capital_variable": description.get("capitalVariable"),
            "duree_personne_morale": description.get("duree"),
            "date_cloture_exercice": description.get("dateClotureExerciceSocial"),
            "siege": _format_adresse(adresse),
        }

    # Personne physique (entrepreneur individuel)
    identite = pp.get("identite") or {}
    e = identite.get("entrepreneur") or {}
    desc = (e.get("descriptionPersonne") or {})
    adresse = ((pp.get("adresseEntreprise") or {}).get("adresse")) or {}
    return {
        "type_personne": "physique",
        "siren": _siren(formality),
        "denomination": " ".join(
            filter(None, [*(desc.get("prenoms") or []), desc.get("nom")])
        )
        or None,
        "forme_juridique_code": "1000",
        "forme_juridique": "Entrepreneur individuel",
        "siege": _format_adresse(adresse),
    }


def parse_resultat_recherche(formality: dict) -> dict:
    """Résumé condensé d'une entreprise pour une liste de résultats de recherche."""
    fiche = parse_fiche(formality)
    siege = fiche.get("siege") or {}
    return {
        "siren": fiche.get("siren"),
        "denomination": fiche.get("denomination"),
        "forme_juridique": fiche.get("forme_juridique"),
        "forme_juridique_code": fiche.get("forme_juridique_code"),
        "commune": siege.get("commune"),
        "code_postal": siege.get("code_postal"),
    }


def _format_adresse(adresse: dict) -> dict | None:
    if not adresse:
        return None
    ligne = " ".join(
        str(p)
        for p in [
            adresse.get("numVoie"),
            adresse.get("typeVoie"),
            adresse.get("voie"),
        ]
        if p
    ).strip()
    return {
        "ligne": ligne or None,
        "complement": adresse.get("complementLocalisation"),
        "code_postal": adresse.get("codePostal"),
        "commune": adresse.get("commune"),
        "code_insee_commune": adresse.get("codeInseeCommune"),
        "pays": adresse.get("codePays") or "FRA",
    }


# --------------------------------------------------------------------------- #
# Dirigeants / mandataires
# --------------------------------------------------------------------------- #

def parse_dirigeants(formality: dict) -> list[dict]:
    pm = _personne_morale(formality)
    pouvoirs = (pm.get("composition") or {}).get("pouvoirs") or []
    out: list[dict] = []
    for p in pouvoirs:
        out.append(_parse_mandataire(p))
    return out


def _parse_mandataire(p: dict) -> dict:
    role_code = p.get("roleEntreprise")
    second_role = p.get("secondRoleEntreprise")
    individu = p.get("individu") or {}
    entreprise = p.get("entreprise") or {}

    base: dict[str, Any] = {
        "qualite_code": role_code,
        "qualite": libelle_role(role_code) or (f"Rôle {role_code}" if role_code else None),
        "second_role_code": second_role,
        "second_role": libelle_role(second_role),
        "beneficiaire_effectif": bool(p.get("beneficiaireEffectif")),
    }

    if individu:
        desc = individu.get("descriptionPersonne") or {}
        base.update(
            {
                "type": "personne_physique",
                "nom": desc.get("nom") or desc.get("nomUsage"),
                "prenoms": desc.get("prenoms") or [],
                "date_naissance": desc.get("dateDeNaissance"),
                "nationalite": desc.get("nationalite"),
            }
        )
    elif entreprise:
        base.update(
            {
                "type": "personne_morale",
                "denomination": entreprise.get("denomination"),
                "siren": entreprise.get("siren"),
                "forme_juridique_code": entreprise.get("formeJuridique"),
                "forme_juridique": libelle_forme_juridique(entreprise.get("formeJuridique")),
            }
        )
    else:
        base["type"] = "inconnu"
    return base


# --------------------------------------------------------------------------- #
# Bénéficiaires effectifs (UBO)
# --------------------------------------------------------------------------- #

def parse_beneficiaires(formality: dict) -> list[dict]:
    """UBO : présents soit dans un bloc dédié `beneficiairesEffectifs`,
    soit signalés dans `pouvoirs[]` via le flag `beneficiaireEffectif`.
    """
    pm = _personne_morale(formality)
    out: list[dict] = []

    for b in pm.get("beneficiairesEffectifs") or []:
        benef = b.get("beneficiaire") or b
        desc = (benef.get("individu") or {}).get("descriptionPersonne") or benef.get(
            "descriptionPersonne"
        ) or {}
        modalite = b.get("modaliteControle") or benef.get("modaliteControle") or {}
        out.append(
            {
                "source": "bloc_beneficiaires_effectifs",
                "nom": desc.get("nom") or desc.get("nomUsage"),
                "prenoms": desc.get("prenoms") or [],
                "date_naissance": desc.get("dateDeNaissance"),
                "nationalite": desc.get("nationalite"),
                "pays_residence": (benef.get("adresseDomicile") or {}).get("codePays"),
                "modalite_controle": modalite or None,
                "detention_capital_pct": modalite.get("detentionPartCapital")
                if isinstance(modalite, dict)
                else None,
                "detention_vote_pct": modalite.get("detentionDroitVote")
                if isinstance(modalite, dict)
                else None,
            }
        )

    if out:
        return out

    # Repli : flag beneficiaireEffectif dans pouvoirs[].
    for d in parse_dirigeants(formality):
        if d.get("beneficiaire_effectif") and d.get("type") == "personne_physique":
            out.append(
                {
                    "source": "flag_pouvoirs",
                    "nom": d.get("nom"),
                    "prenoms": d.get("prenoms"),
                    "date_naissance": d.get("date_naissance"),
                    "nationalite": d.get("nationalite"),
                    "modalite_controle": None,
                }
            )
    return out


# --------------------------------------------------------------------------- #
# Statut (actif / radié / cessation)
# --------------------------------------------------------------------------- #

_KEYWORDS = {
    "radie": ("radiation",),
    "liquidation": ("liquidation",),
    "dissolution": ("dissolution", "dissout", "dissous"),
    "cessation": ("cessation", "cessé", "cesse"),
    "sommeil": ("sommeil",),
}


def parse_statut(formality: dict) -> dict:
    content = _content(formality)
    pm = _personne_morale(formality)
    historique = (formality.get("formality", formality)).get("historique") or content.get(
        "historique"
    ) or []

    evidences: list[str] = []
    flags: set[str] = set()

    for ev in historique:
        libelle = (ev.get("libelleEvenement") or "").lower()
        for flag, kws in _KEYWORDS.items():
            if any(k in libelle for k in kws):
                flags.add(flag)
                evidences.append(
                    f"{ev.get('dateIntegration', '?')} : {ev.get('libelleEvenement')}"
                )

    detail_cessation = pm.get("detailCessationEntreprise") or {}
    evenement_cessation = content.get("evenementCessation")
    nature_cessation = content.get("natureCessation")
    if evenement_cessation or nature_cessation:
        flags.add("cessation")
        if nature_cessation:
            evidences.append(f"natureCessation : {nature_cessation}")

    if "radie" in flags:
        statut = "radié"
    elif "liquidation" in flags or "dissolution" in flags:
        statut = "en dissolution / liquidation"
    elif "sommeil" in flags:
        statut = "en sommeil"
    elif "cessation" in flags:
        statut = "en cessation d'activité"
    else:
        statut = "actif"

    return {
        "siren": _siren(formality),
        "statut": statut,
        "indicateurs": sorted(flags) or None,
        "evidences": evidences or None,
        "detail_cessation": detail_cessation or None,
    }


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
