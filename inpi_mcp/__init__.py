"""Serveur MCP exposant des outils juridiques basés sur les API gratuites de l'INPI.

Sources :
- RNE  : https://registre-national-entreprises.inpi.fr/api  (identité, dirigeants, UBO, statut)
- BODACC : https://bodacc-datadila.opendatasoft.com         (procédures collectives)
- API PI Marques : https://api-gateway.inpi.fr/services/apidiffusion (portefeuilles de marques)
"""

__version__ = "1.1.0"  # 1.1.0 : injection du token dans l'URL /messages SSE (fix 32600)
