"""Client BODACC via l'API Opendatasoft (DILA) — open data, sans authentification.

Dataset : annonces-commerciales
Base    : https://bodacc-datadila.opendatasoft.com/api/explore/v2.1

Filtre par SIREN sur le champ `registre` (tableau ["552 081 317", "552081317"]).
Le champ `jugement` (détail de la procédure) est une CHAÎNE JSON encodée :
il faut un second json.loads() côté client.
"""
from __future__ import annotations

import json
import logging

import httpx

log = logging.getLogger("inpi_mcp.bodacc")

DATASET_URL = (
    "https://bodacc-datadila.opendatasoft.com"
    "/api/explore/v2.1/catalog/datasets/annonces-commerciales/records"
)


class BodaccError(RuntimeError):
    pass


class BodaccClient:
    def __init__(self, timeout: float = 15.0) -> None:
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(timeout, connect=5.0),
            headers={"Accept": "application/json"},
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def annonces_par_siren(
        self, siren: str, famille: str | None = "collective", limit: int = 100
    ) -> list[dict]:
        """Récupère les annonces BODACC d'un SIREN.

        famille=None -> toutes familles ; "collective" -> procédures collectives.
        Les champs `jugement` et `listepersonnes` sont parsés depuis leur encodage JSON.
        """
        where = f'registre="{siren}"'
        if famille:
            where += f' and familleavis="{famille}"'

        params = {
            "where": where,
            "order_by": "dateparution DESC",
            "limit": min(limit, 100),
        }
        resp = await self._client.get(DATASET_URL, params=params)
        if resp.status_code == 429:
            raise BodaccError("Quota Opendatasoft dépassé. Réessayez plus tard.")
        resp.raise_for_status()
        records = resp.json().get("results", [])

        for rec in records:
            rec["jugement"] = _safe_json(rec.get("jugement"))
            rec["listepersonnes"] = _safe_json(rec.get("listepersonnes"))
        return records


def _safe_json(value):
    """Décode une chaîne JSON encodée ; renvoie la valeur telle quelle sinon."""
    if isinstance(value, str) and value.strip():
        try:
            return json.loads(value)
        except (json.JSONDecodeError, ValueError):
            return value
    return value
