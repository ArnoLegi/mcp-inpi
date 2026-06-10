"""Client de l'API PI « Données Marques » de l'INPI (api-gateway.inpi.fr).

Particularités d'authentification (≠ API RNE) :
  1. GET  /services/uaa/api/authenticate  -> pose un cookie XSRF-TOKEN
  2. POST /auth/login {username,password,rememberMe}  + header X-XSRF-TOKEN
        -> pose les cookies access_token / refresh_token
  3. Les appels suivants s'appuient sur ces cookies (httpx.AsyncClient les conserve)
     + le header X-XSRF-TOKEN.

Le compte est un COMPTE TECHNIQUE distinct du compte RNE (créé à l'activation des
« APIs PI » sur data.inpi.fr).

Doc : « API Propriété Industrielle (PI) » v1.0 (INPI).
"""
from __future__ import annotations

import asyncio
import logging

import httpx

log = logging.getLogger("inpi_mcp.marques")

BASE_URL = "https://api-gateway.inpi.fr"
AUTHENTICATE_PATH = "/services/uaa/api/authenticate"
LOGIN_PATH = "/auth/login"
SEARCH_PATH = "/services/apidiffusion/api/marques/search"
NOTICE_PATH = "/services/apidiffusion/api/marques/notice/{ident}"
IMAGE_PATH = "/services/apidiffusion/api/marques/image/{ident}/std"

# Champs renvoyés par /search (au-delà du jeu par défaut).
DEFAULT_FIELDS = [
    "ApplicationNumber",
    "Mark",
    "ClassNumber",
    "MarkCurrentStatusCode",
    "ApplicationDate",
    "ExpiryDate",
    "RegistrationDate",
    "DEPOSANT",
    "DEPOTIT",
    "ApplicantIdentifier",
    "MarkFeature",
    "ukey",
]


class MarquesError(RuntimeError):
    pass


class MarquesNotFound(MarquesError):
    pass


class MarquesClient:
    def __init__(self, username: str, password: str, timeout: float = 30.0) -> None:
        self._username = username
        self._password = password
        self._authenticated = False
        self._lock = asyncio.Lock()
        # Un x-forwarded-for est indiqué « indispensable » par la doc pour les quotas.
        self._client = httpx.AsyncClient(
            base_url=BASE_URL,
            timeout=timeout,
            headers={"Accept": "application/json", "X-Forwarded-For": "127.0.0.1"},
            follow_redirects=True,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    @property
    def _xsrf(self) -> str | None:
        return self._client.cookies.get("XSRF-TOKEN")

    async def _login(self) -> None:
        if not self._username or not self._password:
            raise MarquesError(
                "Identifiants API PI manquants : définissez INPI_PI_USERNAME/"
                "INPI_PI_PASSWORD (ou INPI_USERNAME/INPI_PASSWORD)."
            )
        # 1) Récupération du cookie XSRF-TOKEN.
        await self._client.get(AUTHENTICATE_PATH)
        xsrf = self._xsrf
        if not xsrf:
            raise MarquesError("Cookie XSRF-TOKEN non obtenu depuis l'API PI.")

        # 2) Login : pose les cookies access_token / refresh_token.
        resp = await self._client.post(
            LOGIN_PATH,
            json={
                "username": self._username,
                "password": self._password,
                "rememberMe": True,
            },
            headers={"X-XSRF-TOKEN": xsrf},
        )
        if resp.status_code in (401, 403):
            raise MarquesError("Authentification API PI refusée (compte technique).")
        resp.raise_for_status()
        self._authenticated = True
        log.info("Session API PI Marques ouverte.")

    async def _ensure_auth(self) -> None:
        async with self._lock:
            if not self._authenticated:
                await self._login()

    async def _request(self, method: str, path: str, **kwargs) -> httpx.Response:
        await self._ensure_auth()
        headers = kwargs.pop("headers", {})
        if self._xsrf:
            headers.setdefault("X-XSRF-TOKEN", self._xsrf)
        resp = await self._client.request(method, path, headers=headers, **kwargs)

        if resp.status_code in (401, 403):  # session expirée : on relogge une fois
            log.info("Session API PI expirée, renouvellement.")
            async with self._lock:
                self._authenticated = False
            await self._ensure_auth()
            if self._xsrf:
                headers["X-XSRF-TOKEN"] = self._xsrf
            resp = await self._client.request(method, path, headers=headers, **kwargs)
        return resp

    async def search_par_siren(
        self, siren: str, collections: list[str] | None = None, size: int = 100
    ) -> list[dict]:
        """Recherche les marques d'un titulaire par SIREN (ApplicantIdentifier).

        Le SIREN n'est rattaché qu'aux marques FR (déposant / dernier titulaire ayant
        renouvelé) ; les collections EU/WO n'ont pas de SIREN.
        """
        body = {
            "collections": collections or ["FR"],
            "query": f"[ApplicantIdentifier={siren}]",
            "fields": DEFAULT_FIELDS,
            "size": min(size, 200),
            "position": 0,
        }
        resp = await self._request("POST", SEARCH_PATH, json=body)
        if resp.status_code == 429:
            raise MarquesError("Quota API PI dépassé. Réessayez plus tard.")
        resp.raise_for_status()
        return _extract_results(resp.json())

    async def notice(self, ident: str) -> dict:
        """Détail d'une marque par identifiant (collection+numéro, ex. 'FR4216963').

        On force Accept: application/json (la notice est en XML par défaut).
        """
        resp = await self._request(
            "GET",
            NOTICE_PATH.format(ident=ident),
            headers={"Accept": "application/json"},
        )
        if resp.status_code == 404:
            raise MarquesNotFound(f"Marque {ident} introuvable.")
        if resp.status_code == 429:
            raise MarquesError("Quota API PI dépassé. Réessayez plus tard.")
        resp.raise_for_status()
        try:
            return resp.json()
        except ValueError:
            # Certaines notices ne renvoient que du XML : on remonte le brut.
            return {"_raw_xml": resp.text}

    def image_url(self, ident: str) -> str:
        """URL de l'image/logo standard d'une marque (binaire PNG, accès authentifié)."""
        return f"{BASE_URL}{IMAGE_PATH.format(ident=ident)}"


def _extract_results(payload) -> list[dict]:
    """Normalise la réponse /search (la forme exacte peut varier selon l'API)."""
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for key in ("result", "results", "hits", "marques", "data"):
            val = payload.get(key)
            if isinstance(val, list):
                return val
            if isinstance(val, dict) and isinstance(val.get("hits"), list):
                return val["hits"]
    return []
