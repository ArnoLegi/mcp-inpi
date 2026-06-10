"""Middleware ASGI d'authentification pour protéger l'endpoint MCP.

Si une clé est configurée (MCP_API_KEY), toute requête HTTP doit présenter le token,
soit via le header `Authorization: Bearer <clé>`, soit via le paramètre d'URL
`?token=<clé>` (pratique pour les clients ne gérant pas les en-têtes, comme OpenLégi).
Les chemins exemptés (healthchecks) restent ouverts. Si aucune clé n'est configurée,
le middleware laisse tout passer (endpoint ouvert).

Cas particulier du transport SSE : à la connexion `/sse`, le serveur annonce au client
une URL de POST `/messages/?session_id=...` qui ne reprend pas la query string initiale.
Quand le token a été fourni via `?token=`, ce middleware réécrit cette URL annoncée pour
y réinjecter `&token=...`, sinon les POST `/messages/` suivants seraient rejetés (401)
et la session se terminerait (« Session terminated », code 32600).
"""
from __future__ import annotations

import re
import secrets
from urllib.parse import parse_qs, quote

from starlette.responses import JSONResponse

# Chemins toujours accessibles sans authentification (healthchecks Railway).
EXEMPT_PATHS = frozenset({"/", "/health"})

# Repère l'URL annoncée dans l'event SSE "endpoint" pour y greffer le token.
_SESSION_ID_RE = re.compile(rb"session_id=[0-9a-fA-F\-]+")


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

        token, from_query = self._extraire_token(scope)
        if not (token and secrets.compare_digest(token, self.api_key)):
            await self._refuser(send)
            return

        # Token fourni via l'URL : on le réinjecte dans l'endpoint /messages/ annoncé
        # par le flux SSE, pour que les POST ultérieurs restent authentifiés.
        if from_query:
            send = self._wrap_send_inject_token(send, token)

        await self.app(scope, receive, send)

    def _extraire_token(self, scope) -> tuple[str, bool]:
        """Renvoie (token, provenait_de_l_url).

        Priorité au header Bearer ; à défaut, paramètre d'URL ?token=.
        """
        headers = dict(scope.get("headers") or [])
        auth = headers.get(b"authorization", b"").decode("latin-1")
        prefix = "bearer "
        if auth.lower().startswith(prefix):
            tok = auth[len(prefix):].strip()
            if tok:
                return tok, False

        query = parse_qs((scope.get("query_string") or b"").decode("latin-1"))
        values = query.get("token") or []
        return (values[0].strip() if values else ""), True

    def _wrap_send_inject_token(self, send, token: str):
        token_q = quote(token, safe="").encode("latin-1")

        async def wrapped(message):
            if message.get("type") == "http.response.body":
                body = message.get("body", b"")
                if b"session_id=" in body and b"token=" not in body:
                    body = _SESSION_ID_RE.sub(
                        lambda m: m.group(0) + b"&token=" + token_q, body
                    )
                    message = {**message, "body": body}
            await send(message)

        return wrapped

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
