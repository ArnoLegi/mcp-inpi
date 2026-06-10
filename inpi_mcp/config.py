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


def _clean_secret(name: str) -> str:
    """Lit une variable d'environnement en retirant espaces et guillemets parasites.

    Erreur fréquente sur Railway/Replit : coller la valeur entourée de guillemets
    (INPI_PASSWORD="xxx"), qui sont alors stockés littéralement et provoquent un 401.
    """
    value = os.environ.get(name, "").strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        value = value[1:-1].strip()
    return value


def load_settings() -> Settings:
    username = _clean_secret("INPI_USERNAME")
    password = _clean_secret("INPI_PASSWORD")

    pi_username = _clean_secret("INPI_PI_USERNAME") or username
    pi_password = _clean_secret("INPI_PI_PASSWORD") or password

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
