"""Tarifs des modèles, en dollars par million de tokens.

Relevé le 2026-08-23 sur https://developers.openai.com/api/docs/pricing
À revérifier avant toute publication du tableau comparatif du README.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Price:
    input_: float
    cached_input: float
    output: float


PRICING_USD_PER_MTOK: dict[str, Price] = {
    "gpt-5.6-sol": Price(4.00, 0.40, 20.00),
    "gpt-5.6-terra": Price(2.00, 0.20, 12.00),
    "gpt-5.6-luna": Price(0.20, 0.02, 1.20),
    "gpt-5.5": Price(5.00, 0.50, 30.00),
    "gpt-5.4": Price(2.50, 0.25, 15.00),
    "gpt-5.4-mini": Price(0.75, 0.075, 4.50),
    "gpt-5.4-nano": Price(0.20, 0.02, 1.25),
    "gpt-5": Price(1.25, 0.125, 10.00),
    "gpt-5-mini": Price(0.25, 0.025, 2.00),
    "gpt-5-nano": Price(0.05, 0.005, 0.40),
    "gpt-4.1": Price(2.00, 0.50, 8.00),
    "gpt-4.1-mini": Price(0.40, 0.10, 1.60),
    "gpt-4.1-nano": Price(0.10, 0.025, 0.40),
    "gpt-4o-mini": Price(0.15, 0.075, 0.60),
    # Servis par Groq, via OPENAI_BASE_URL. Tarif catalogue Groq relevé le
    # 2026-08-25 : le palier Developer ne facture rien, mais publier un coût nul
    # répondrait à « ce que j'ai payé », pas à « ce que ce système coûterait en
    # production » — qui est la question posée par le tableau du README.
    "openai/gpt-oss-120b": Price(0.15, 0.075, 0.60),
}


class UnknownModelPricing(KeyError):
    """Levée plutôt que de renvoyer un coût de zéro.

    Le coût par requête est une colonne du tableau d'évaluation : un zéro
    silencieux serait un chiffre faux publié dans un README.
    """


def estimate_cost(
    model: str,
    input_tokens: int,
    cached_input_tokens: int,
    output_tokens: int,
) -> float:
    try:
        price = PRICING_USD_PER_MTOK[model]
    except KeyError as exc:
        raise UnknownModelPricing(
            f"Aucun tarif connu pour « {model} ». Ajoute-le à PRICING_USD_PER_MTOK, "
            f"au tarif catalogue du fournisseur qui le sert."
        ) from exc

    # `usage.input_tokens` d'OpenAI inclut déjà les tokens servis par le cache
    # de prompt : on les retire pour ne pas les facturer au plein tarif.
    fresh_input = max(input_tokens - cached_input_tokens, 0)
    return (
        fresh_input * price.input_
        + cached_input_tokens * price.cached_input
        + output_tokens * price.output
    ) / 1_000_000
