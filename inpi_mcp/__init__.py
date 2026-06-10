"""Serveur MCP exposant des outils juridiques sur les entreprises françaises.

Sources (toutes gratuites) :
- Recherche d'Entreprises : https://recherche-entreprises.api.gouv.fr (identité, dirigeants,
  statut, recherche — sans clé)
- BODACC : https://bodacc-datadila.opendatasoft.com (procédures collectives — sans clé)
- API PI Marques : https://api-gateway.inpi.fr/services/apidiffusion (marques — identifiants INPI)
"""

__version__ = "1.5.0"  # 1.5.0 : identité/dirigeants/statut via l'API Recherche d'Entreprises
