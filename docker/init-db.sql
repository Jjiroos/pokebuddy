-- pgvector n'est peuplé qu'au jalon 3 (RAG), mais activer l'extension
-- maintenant coûte zéro et évite une migration de plus plus tard.
CREATE EXTENSION IF NOT EXISTS vector;
