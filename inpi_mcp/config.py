"""Chargement de la configuration depuis les variables d'environnement."""
from __future__ import annotations

import os
from dataclasses import dataclass

try:
    from dotenv import load_dotenv

    load_dotenv()  # charge un éventuel .env local (no-op si absent / sur Replit Secrets)
except ImportError:  # python-dotenv non installé : on lit l'environnement tel quel
    pass


class ConfigError(RuntimeError):
    """Configuration manquante ou invalide."""


@dataclass(frozen=True)
class Settings:
    inpi_username: str
    inpi_password: str
    # Compte technique API PI Marques ; retombe sur le compte RNE si absent.
    pi_username: str
    pi_password: str
    host: str
    port: int

    @property
    def has_inpi_credentials(self) -> bool:
        return bool(self.inpi_username and self.inpi_password)


def load_settings() -> Settings:
    username = os.environ.get("INPI_USERNAME", "").strip()
    password = os.environ.get("INPI_PASSWORD", "").strip()

    pi_username = os.environ.get("INPI_PI_USERNAME", "").strip() or username
    pi_password = os.environ.get("INPI_PI_PASSWORD", "").strip() or password

    host = os.environ.get("HOST", "0.0.0.0").strip() or "0.0.0.0"
    port = int(os.environ.get("PORT", "8080"))

    return Settings(
        inpi_username=username,
        inpi_password=password,
        pi_username=pi_username,
        pi_password=pi_password,
        host=host,
        port=port,
    )


settings = load_settings()
