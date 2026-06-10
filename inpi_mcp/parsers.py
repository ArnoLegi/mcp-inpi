"""Extraction des données métier pour les annonces BODACC et les marques INPI.

(L'identité / dirigeants / statut proviennent de l'API Recherche d'Entreprises et sont
traités dans `entreprises_parsers.py`.) Parsing défensif via `.get`.
"""
from __future__ import annotations

import html
import xml.etree.ElementTree as ET

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


def _clean(value):
    """Déséchappe les entités HTML d'une valeur texte (ex. `&apos;`, `&amp;`)."""
    if isinstance(value, str):
        return html.unescape(value)
    return value


def _fmt_date(value):
    """Formate une date INPI `YYYYMMDD` en `YYYY-MM-DD` ; sinon renvoie tel quel."""
    if isinstance(value, str) and len(value) == 8 and value.isdigit():
        return f"{value[0:4]}-{value[4:6]}-{value[6:8]}"
    return value


def _flatten_fields(hit: dict) -> dict[str, object]:
    """Aplatit le tableau `fields` ([{name, value, values}, …]) en dict {name: …}.

    La réponse /search de l'API PI imbrique les champs dans une liste ; chaque entrée
    porte `value` (scalaire) ou `values` (liste, ex. classes de Nice multiples). Les
    noms peuvent être dupliqués : on conserve la première occurrence non vide.
    """
    out: dict[str, object] = {}
    for field in hit.get("fields") or []:
        if not isinstance(field, dict):
            continue
        name = field.get("name")
        if not name or name in out:
            continue
        value = field.get("value")
        values = field.get("values")
        if value not in (None, ""):
            out[name] = _clean(value)
        elif isinstance(values, list) and values:
            out[name] = [_clean(v) for v in values]
    return out


def _identifiant(hit: dict) -> str | None:
    """Identifiant marque (collection+numéro, ex. 'FR4256170') depuis href ou documentId."""
    href = (hit.get("xml") or {}).get("href") if isinstance(hit.get("xml"), dict) else None
    if isinstance(href, str) and "/" in href:
        ident = href.rstrip("/").rsplit("/", 1)[-1]
        if ident:
            return ident
    doc = hit.get("documentId")
    return f"FR{doc}" if doc else None


def parse_marque_hit(hit: dict) -> dict:
    """Synthèse d'une marque depuis un résultat de /search (champs ST.66).

    Les hits ont la forme {documentId, xml:{href}, image:{href}, fields:[{name,value,values}]}.
    On tolère aussi un dict déjà aplati (rétro-compat / autres formats).
    """
    flat = _flatten_fields(hit) if isinstance(hit.get("fields"), list) else hit

    classes = _first(flat, "ClassNumber", "classNumber", default=[])
    if isinstance(classes, (str, int)):
        classes = [str(classes)]

    return {
        "numero_national": _first(flat, "ApplicationNumber", "applicationNumber"),
        "identifiant": _identifiant(hit) or _first(flat, "ukey", "ApplicationNumber"),
        "denomination": _first(flat, "Mark", "mark"),
        "statut": _first(flat, "MarkCurrentStatusCode", "markCurrentStatusCode"),
        "date_depot": _fmt_date(_first(flat, "ApplicationDate", "applicationDate")),
        "date_enregistrement": _fmt_date(_first(flat, "RegistrationDate", "registrationDate")),
        "date_expiration": _fmt_date(_first(flat, "ExpiryDate", "expiryDate")),
        "classes_nice": classes or None,
        "type_marque": _first(flat, "MarkFeature", "markFeature"),
        "deposant": _first(flat, "DEPOSANT", "deposant"),
        "titulaire": _first(flat, "DEPOTIT", "depotit"),
        "siren_titulaire": _first(flat, "ApplicantIdentifier", "applicantIdentifier"),
    }


def _xml_text(node, *paths):
    """Premier texte non vide trouvé parmi `paths` (relatifs à `node`)."""
    if node is None:
        return None
    for path in paths:
        el = node.find(path)
        if el is not None and el.text and el.text.strip():
            return _clean(el.text.strip())
    return None


def parse_marque_notice(xml_text: str) -> dict:
    """Parse une notice marque XML ST.66 (`<TradeMark>`) en synthèse structurée.

    Le endpoint /notice de l'API PI ne renvoie que du XML ST.66. En cas de XML
    illisible, on remonte le brut sous `_raw_xml` plutôt que de lever.
    """
    if not xml_text:
        return {}
    try:
        # On repasse en bytes : ElementTree refuse une str portant une déclaration
        # d'encodage (« encoding declaration are not supported »).
        root = ET.fromstring(xml_text.encode("utf-8"))
    except ET.ParseError:
        return {"_raw_xml": xml_text}

    classes = []
    for cd in root.findall(".//GoodsServices/ClassDescriptionDetails/ClassDescription"):
        num = _xml_text(cd, "ClassNumber")
        if num:
            classes.append({
                "classe": num,
                "libelle": _xml_text(cd, "GoodsServicesDescription"),
            })

    applicant = root.find(".//ApplicantDetails/Applicant")
    titulaire = siren = adresse = forme_juridique = None
    if applicant is not None:
        forme_juridique = _xml_text(applicant, "ApplicantLegalEntity")
        titulaire = _xml_text(
            applicant, ".//FormattedName/OrganizationName", ".//FormattedName/LastName"
        )
        siren = _xml_text(applicant, ".//FormattedName/IndividualIdentifier")
        addr = applicant.find(".//FormattedAddress")
        if addr is not None:
            parts = [
                _xml_text(addr, "AddressStreet"),
                _xml_text(addr, "AddressPostcode"),
                _xml_text(addr, "AddressCity"),
            ]
            adresse = " ".join(p for p in parts if p) or None

    return {
        "numero_national": _xml_text(root, "ApplicationNumber"),
        "collection": _xml_text(root, "RegistrationOfficeCode"),
        "denomination": _xml_text(root, ".//WordMarkSpecification/MarkVerbalElementText"),
        "type_marque": _xml_text(root, "MarkFeature"),
        "statut": _xml_text(root, "MarkCurrentStatusCode"),
        "date_depot": _xml_text(root, "ApplicationDate"),
        "date_enregistrement": _xml_text(root, "RegistrationDate"),
        "date_expiration": _xml_text(root, "ExpiryDate"),
        "lieu_depot": _xml_text(root, "FilingPlace"),
        "classes_nice": classes or None,
        "titulaire": titulaire,
        "forme_juridique": forme_juridique,
        "siren_titulaire": siren,
        "adresse_titulaire": adresse,
    }
