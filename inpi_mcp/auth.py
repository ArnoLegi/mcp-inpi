"""Middleware ASGI d'authentification Bearer pour protéger l'endpoint MCP.

Si une clé est configurée (MCP_API_KEY), toute requête HTTP doit présenter
`Authorization: Bearer <clé>`, sauf les chemins exemptés (healthchecks).
Si aucune clé n'est configurée, le middleware laisse tout passer (endpoint ouvert).
"""
from __future__ import annotations

import secrets

from starlette.responses import JSONResponse

# Chemins toujours accessibles sans authentification (healthchecks Railway).
EXEMPT_PATHS = frozenset({"/", "/health"})


class BearerAuthMiddleware:
    def __init__(self, app, api_key: str, exempt_paths=EXEMPT_PATHS) -> None:
        self.app = app
        self.api_key = api_key or ""
        self.exempt_paths = frozenset(exempt_paths)

    async def __call__(self, scope, receive, send) -> None:
        # Auth désactivée, requête non-HTTP, ou chemin exempté : on laisse passer.
        if (
            not self.api_key
            or scope.get("type") != "http"
            or scope.get("path", "") in self.exempt_paths
        ):
            await self.app(scope, receive, send)
            return

        if self._token_valide(scope):
            await self.app(scope, receive, send)
            return

        await self._refuser(send)

    def _token_valide(self, scope) -> bool:
        headers = dict(scope.get("headers") or [])
        auth = headers.get(b"authorization", b"").decode("latin-1")
        prefix = "bearer "
        if not auth.lower().startswith(prefix):
            return False
        token = auth[len(prefix):].strip()
        # Comparaison à temps constant pour éviter les attaques temporelles.
        return bool(token) and secrets.compare_digest(token, self.api_key)

    async def _refuser(self, send) -> None:
        response = JSONResponse(
            {"erreur": "Non autorisé : header 'Authorization: Bearer <MCP_API_KEY>' requis."},
            status_code=401,
            headers={"WWW-Authenticate": "Bearer"},
        )
        await response(  # type: ignore[arg-type]
            {"type": "http"}, _empty_receive, send
        )


async def _empty_receive():
    return {"type": "http.request", "body": b"", "more_body": False}
