"""Client de l'API Recherche d'Entreprises (recherche-entreprises.api.gouv.fr).

API publique gratuite, sans clé, basée sur SIRENE + RNE (data.gouv.fr / DINUM).
Utilisée pour : fiche société, dirigeants, statut, recherche par dénomination.
(BODACC et marques restent sur l'INPI.)

Endpoint unique : GET /search?q={texte|siren}&page={n}&per_page={m}
"""
from __future__ import annotations

import logging

import httpx

log = logging.getLogger("inpi_mcp.recherche_entreprises")

BASE_URL = "https://recherche-entreprises.api.gouv.fr"


class RechercheEntreprisesError(RuntimeError):
    """Erreur renvoyée par l'API Recherche d'Entreprises."""


class EntrepriseNotFound(RechercheEntreprisesError):
    """SIREN / entreprise introuvable."""


class RechercheEntreprisesClient:
    def __init__(self, timeout: float = 10.0) -> None:
        self._client = httpx.AsyncClient(
            base_url=BASE_URL,
            timeout=httpx.Timeout(timeout, connect=5.0),
            headers={"Accept": "application/json"},
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def search(self, q: str, page: int = 1, per_page: int = 10) -> dict:
        """Recherche brute (texte libre ou SIREN)."""
        resp = await self._client.get(
            "/search",
            params={"q": q, "page": max(1, page), "per_page": max(1, min(per_page, 25))},
        )
        if resp.status_code == 429:
            raise RechercheEntreprisesError(
                "Quota de l'API Recherche d'Entreprises dépassé. Réessayez plus tard."
            )
        resp.raise_for_status()
        return resp.json()

    async def par_siren(self, siren: str) -> dict:
        """Renvoie l'entreprise correspondant exactement au SIREN."""
        data = await self.search(siren, per_page=10)
        for result in data.get("results") or []:
            if result.get("siren") == siren:
                return result
        raise EntrepriseNotFound(f"SIREN {siren} introuvable.")
