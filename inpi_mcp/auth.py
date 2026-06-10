"""Middleware ASGI d'authentification pour protéger l'endpoint MCP.

Si une clé est configurée (MCP_API_KEY), toute requête HTTP doit présenter le token,
soit via le header `Authorization: Bearer <clé>`, soit via le paramètre d'URL
`?token=<clé>` (pratique pour les clients ne gérant pas les en-têtes, comme OpenLégi).
Les chemins exemptés (healthchecks) restent ouverts. Si aucune clé n'est configurée,
le middleware laisse tout passer (endpoint ouvert).
"""
from __future__ import annotations

import secrets
from urllib.parse import parse_qs

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
        token = self._extraire_token(scope)
        # Comparaison à temps constant pour éviter les attaques temporelles.
        return bool(token) and secrets.compare_digest(token, self.api_key)

    def _extraire_token(self, scope) -> str:
        """Récupère le token depuis le header Bearer, sinon le paramètre d'URL ?token=."""
        headers = dict(scope.get("headers") or [])
        auth = headers.get(b"authorization", b"").decode("latin-1")
        prefix = "bearer "
        if auth.lower().startswith(prefix):
            tok = auth[len(prefix):].strip()
            if tok:
                return tok

        query = parse_qs((scope.get("query_string") or b"").decode("latin-1"))
        values = query.get("token") or []
        return values[0].strip() if values else ""

    async def _refuser(self, send) -> None:
        response = JSONResponse(
            {
                "erreur": "Non autorisé : fournissez la clé via le header "
                "'Authorization: Bearer <MCP_API_KEY>' ou le paramètre d'URL '?token=<MCP_API_KEY>'."
            },
            status_code=401,
            headers={"WWW-Authenticate": "Bearer"},
        )
        await response(  # type: ignore[arg-type]
            {"type": "http"}, _empty_receive, send
        )


async def _empty_receive():
    return {"type": "http.request", "body": b"", "more_body": False}
