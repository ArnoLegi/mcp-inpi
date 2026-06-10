"""Client de l'API RNE (Registre National des Entreprises) de l'INPI.

Sert uniquement à *enrichir* une fiche société (capital social, objet social, greffe
RCS) — données absentes de l'API ouverte Recherche d'Entreprises. L'appel est optionnel
et tolérant à l'échec côté serveur.

Authentification : POST /api/sso/login -> { "token": "<JWT>" }, puis
`Authorization: Bearer <token>` sur les appels suivants ; re-login automatique sur 401.

Doc officielle : « API formalités » (INPI). Le compte est le compte data.inpi.fr
(INPI_USERNAME / INPI_PASSWORD).
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
    """SIREN inconnu au RNE (HTTP 404)."""


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
    def __init__(self, username: str, password: str, timeout: float = 8.0) -> None:
        self._username = username
        self._password = password
        self._token: str | None = None
        self._lock = asyncio.Lock()
        # Timeout court : l'enrichissement RNE ne doit jamais retarder la fiche.
        self._client = httpx.AsyncClient(
            base_url=BASE_URL,
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
                f"{_message_inpi(resp)}."
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
        """Renvoie l'objet `formality` complet pour un SIREN (contenu sous `content`)."""
        resp = await self._request("GET", f"/companies/{siren}")
        if resp.status_code == 404:
            raise RNENotFound(f"SIREN {siren} introuvable au RNE.")
        if resp.status_code == 403:
            raise RNEError(
                f"Accès refusé pour le SIREN {siren} "
                "(données confidentielles ou habilitation insuffisante)."
            )
        if resp.status_code == 429:
            raise RNEError("Quota INPI dépassé. Réessayez plus tard.")
        resp.raise_for_status()
        data = resp.json()
        if isinstance(data, list):
            if not data:
                raise RNENotFound(f"SIREN {siren} introuvable au RNE.")
            data = data[0]
        return data
