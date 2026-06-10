"""Point d'entrée du serveur MCP INPI — déployable sur Railway.

Deux transports MCP sont exposés (endpoints publics, sans authentification) :
  - Streamable HTTP : /mcp   (RECOMMANDÉ pour Claude.ai)
  - SSE (legacy)    : /sse   (+ /messages/)

Lancement local :  python main.py
Santé           :  http://<hote>:<port>/health
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

import uvicorn
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route

from inpi_mcp import __version__
from inpi_mcp.config import settings
from inpi_mcp.server import mcp

log = logging.getLogger("inpi_mcp.main")


async def health(_request):
    return JSONResponse(
        {
            "status": "ok",
            "service": "mcp-inpi",
            "version": __version__,
            "transports": {"streamable_http": "/mcp", "sse": "/sse"},
            "inpi_credentials_configured": settings.has_inpi_credentials,
        }
    )


def build_app() -> Starlette:
    # On réutilise les routes natives de FastMCP pour chaque transport :
    #   - streamable_app : Route exacte /mcp  (Streamable HTTP)
    #   - sse_app        : Route /sse + Mount /messages/  (SSE legacy)
    # (Remonter /mcp via un Mount casserait le routage interne -> 404.)
    streamable_app = mcp.streamable_http_app()  # crée aussi mcp.session_manager
    sse_app = mcp.sse_app()

    @asynccontextmanager
    async def lifespan(_app):
        # Le transport Streamable HTTP exige que le session manager tourne
        # pendant toute la durée de vie de l'application.
        async with mcp.session_manager.run():
            log.info("Session manager Streamable HTTP démarré (/mcp).")
            yield

    routes = [
        Route("/", health, methods=["GET"]),
        Route("/health", health, methods=["GET"]),
        *streamable_app.routes,  # /mcp
        *sse_app.routes,         # /sse + /messages/
    ]
    return Starlette(routes=routes, lifespan=lifespan)


app = build_app()


if __name__ == "__main__":
    if not settings.has_inpi_credentials:
        log.warning(
            "INPI_USERNAME / INPI_PASSWORD non définis : les outils RNE et Marques "
            "échoueront. Renseignez-les dans .env (local) ou les variables Railway."
        )
    log.info(
        "Démarrage MCP INPI v%s sur http://%s:%s (transports : /mcp, /sse)",
        __version__,
        settings.host,
        settings.port,
    )
    uvicorn.run(app, host=settings.host, port=settings.port, log_level="info")
