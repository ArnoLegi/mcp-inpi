"""Point d'entrée du serveur MCP INPI — transport SSE, déployable sur Railway.

Lancement local :  python main.py
Endpoint MCP/SSE :  http://<hote>:<port>/sse
Santé           :  http://<hote>:<port>/health
"""
from __future__ import annotations

import logging

import uvicorn
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Mount, Route

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
            "transport": "sse",
            "sse_endpoint": "/sse",
            "inpi_credentials_configured": settings.has_inpi_credentials,
        }
    )


def build_app() -> Starlette:
    # L'app SSE de FastMCP expose /sse et /messages/ ; on l'enveloppe pour
    # ajouter / et /health (utilisé par le healthcheck Railway).
    sse_app = mcp.sse_app()
    return Starlette(
        routes=[
            Route("/", health, methods=["GET"]),
            Route("/health", health, methods=["GET"]),
            Mount("/", app=sse_app),
        ]
    )


app = build_app()


if __name__ == "__main__":
    if not settings.has_inpi_credentials:
        log.warning(
            "INPI_USERNAME / INPI_PASSWORD non définis : les outils RNE et Marques "
            "échoueront. Renseignez-les dans .env (local) ou les variables Railway."
        )
    log.info(
        "Démarrage MCP INPI v%s sur http://%s:%s/sse",
        __version__,
        settings.host,
        settings.port,
    )
    uvicorn.run(app, host=settings.host, port=settings.port, log_level="info")
