"""Client de l'API RNE (Registre National des Entreprises) de l'INPI.

Authentification : POST /api/sso/login  -> { "token": "..." }
Le token (JWT) est ensuite passé en `Authorization: Bearer <token>`.
Sa durée de vie n'étant pas documentée, on re-logge automatiquement sur 401.

Doc officielle : « API formalités » v4.0 (INPI, juin 2025).
"""
from __future__ import annotations

import asyncio
import logging

import httpx

log = logging.getLogger("inpi_mcp.rne")

BASE_URL = "https://registre-national-entreprises.inpi.fr/api"


class RNEError(RuntimeError):
    """Erreur renvoyée par l'API RNE."""


class RNENotFound(RNEError):
    """SIREN inconnu (HTTP 404)."""


def _message_inpi(resp: httpx.Response) -> str:
    """Extrait le message d'erreur lisible renvoyé par l'INPI."""
    try:
        data = resp.json()
        if isinstance(data, dict):
            return data.get("message") or data.get("title") or str(data)
    except ValueError:
        pass
    return (resp.text or "").strip()[:200] or "réponse vide"


class RNEClient:
    def __init__(self, username: str, password: str, timeout: float = 30.0) -> None:
        self._username = username
        self._password = password
        self._token: str | None = None
        self._lock = asyncio.Lock()
        self._client = httpx.AsyncClient(
            base_url=BASE_URL,
            # connect court (5s) pour échouer vite si l'INPI est injoignable ;
            # read généreux (30s) car certains gros enregistrements RNE sont lents
            # (le keepalive côté serveur maintient la session client pendant ce temps).
            timeout=httpx.Timeout(timeout, connect=5.0),
            headers={"Accept": "application/json"},
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _login(self) -> str:
        if not self._username or not self._password:
            raise RNEError(
                "Identifiants INPI manquants : définissez INPI_USERNAME et INPI_PASSWORD."
            )
        resp = await self._client.post(
            "/sso/login",
            json={"username": self._username, "password": self._password},
        )
        if resp.status_code in (401, 403):
            raise RNEError(
                f"Authentification INPI refusée (HTTP {resp.status_code}) : "
                f"{_message_inpi(resp)}. Vérifiez INPI_USERNAME (= email exact du compte "
                "data.inpi.fr) et INPI_PASSWORD, et que l'accès « API RNE » est bien activé "
                "sur le compte."
            )
        resp.raise_for_status()
        token = resp.json().get("token")
        if not token:
            raise RNEError("Réponse de login INPI inattendue : champ 'token' absent.")
        log.info("Token RNE obtenu.")
        return token

    async def _ensure_token(self) -> str:
        async with self._lock:
            if self._token is None:
                self._token = await self._login()
            return self._token

    async def _request(self, method: str, path: str, **kwargs) -> httpx.Response:
        token = await self._ensure_token()
        headers = {"Authorization": f"Bearer {token}"}
        resp = await self._client.request(method, path, headers=headers, **kwargs)

        if resp.status_code == 401:  # token expiré : on relogge une fois
            log.info("Token RNE expiré, renouvellement.")
            async with self._lock:
                self._token = None
            token = await self._ensure_token()
            headers = {"Authorization": f"Bearer {token}"}
            resp = await self._client.request(method, path, headers=headers, **kwargs)
        return resp

    async def get_company(self, siren: str) -> dict:
        """Renvoie l'objet `formality` complet pour un SIREN.

        Le contenu métier se trouve sous la clé `content` (cf. parsers.py).
        """
        resp = await self._request("GET", f"/companies/{siren}")
        if resp.status_code == 404:
            raise RNENotFound(f"SIREN {siren} introuvable au RNE.")
        if resp.status_code == 403:
            raise RNEError(
                f"Accès refusé pour le SIREN {siren} "
                "(données potentiellement confidentielles ou habilitation insuffisante)."
            )
        if resp.status_code == 429:
            raise RNEError("Quota INPI dépassé (10 000 requêtes/jour). Réessayez plus tard.")
        resp.raise_for_status()
        data = resp.json()
        # Le endpoint peut renvoyer soit un objet {formality:{...}}, soit directement
        # l'objet formality, soit (endpoint liste) une liste. On normalise.
        if isinstance(data, list):
            if not data:
                raise RNENotFound(f"SIREN {siren} introuvable au RNE.")
            data = data[0]
        return data

    async def search_companies(
        self, company_name: str, page_size: int = 20, page: int = 1
    ) -> list[dict]:
        """Recherche d'entreprises par dénomination sociale.

        GET /api/companies?companyName=...&pageSize=...&page=...
        Renvoie une liste d'objets `formality`.
        """
        params = {
            "companyName": company_name,
            "pageSize": max(1, min(page_size, 100)),
            "page": max(1, page),
        }
        resp = await self._request("GET", "/companies", params=params)
        if resp.status_code == 429:
            raise RNEError("Quota INPI dépassé (10 000 requêtes/jour). Réessayez plus tard.")
        resp.raise_for_status()
        data = resp.json()
        if isinstance(data, dict):
            # Selon les versions, la liste peut être encapsulée.
            for key in ("results", "items", "companies", "data"):
                if isinstance(data.get(key), list):
                    return data[key]
            return [data]
        return data if isinstance(data, list) else []
