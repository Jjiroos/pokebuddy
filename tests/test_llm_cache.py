from __future__ import annotations

import pytest

from src.llm.cache import LLMCache, cache_key
from src.llm.pricing import UnknownModelPricing, estimate_cost

MESSAGES = [{"role": "user", "content": "Combien pèse Dracaufeu ?"}]
BASE = {
    "provider": "openai",
    "model": "gpt-5.6-luna",
    "messages": MESSAGES,
    "params": {"reasoning": {"effort": "low"}},
    "schema_fingerprint": None,
}


def test_cle_stable_entre_deux_appels_identiques():
    assert cache_key(**BASE) == cache_key(**BASE)


def test_cle_insensible_a_l_ordre_des_parametres():
    a = cache_key(**{**BASE, "params": {"reasoning": {"effort": "low"}, "z": 1}})
    b = cache_key(**{**BASE, "params": {"z": 1, "reasoning": {"effort": "low"}}})
    assert a == b


@pytest.mark.parametrize(
    "change",
    [
        {"model": "gpt-5.6-sol"},
        {"messages": [{"role": "user", "content": "autre question"}]},
        {"params": {"reasoning": {"effort": "high"}}},
        {"schema_fingerprint": "src.api.schemas.PokemonFacts"},
        {"provider": "mistral"},
    ],
    ids=["modele", "question", "effort", "schema", "fournisseur"],
)
def test_tout_ce_qui_change_la_reponse_change_la_cle(change):
    assert cache_key(**{**BASE, **change}) != cache_key(**BASE)


def test_le_schema_fait_partie_de_la_cle(tmp_cache_path):
    """Sans cela, deux extractions de formes différentes se répondraient l'une l'autre."""
    cache = LLMCache(tmp_cache_path)
    k1 = cache_key(**{**BASE, "schema_fingerprint": "A"})
    k2 = cache_key(**{**BASE, "schema_fingerprint": "B"})
    cache.put(k1, provider="openai", model="m", payload={"text": "reponse A"})
    assert cache.get(k1) == {"text": "reponse A"}
    assert cache.get(k2) is None


def test_aller_retour_et_absence(tmp_cache_path):
    cache = LLMCache(tmp_cache_path)
    assert cache.get("inconnu") is None
    cache.put("k", provider="openai", model="m", payload={"text": "salut", "n": 3})
    assert cache.get("k") == {"text": "salut", "n": 3}


def test_le_cache_survit_a_la_reouverture(tmp_cache_path):
    LLMCache(tmp_cache_path).put("k", provider="openai", model="m", payload={"text": "x"})
    assert LLMCache(tmp_cache_path).get("k") == {"text": "x"}


def test_journal_des_appels(tmp_cache_path):
    cache = LLMCache(tmp_cache_path)
    for hit, cost in [(False, 0.0004), (True, 0.0)]:
        cache.log_call(
            provider="openai",
            model="gpt-5.6-luna",
            key="k",
            cache_hit=hit,
            input_tokens=100,
            cached_input_tokens=0,
            output_tokens=20,
            cost_usd=cost,
            latency_ms=10,
            run_label="jalon1",
        )
    rows = cache._conn.execute("SELECT cache_hit, cost_usd FROM llm_calls ORDER BY id").fetchall()
    assert [tuple(r) for r in rows] == [(0, 0.0004), (1, 0.0)]
    total = cache._conn.execute("SELECT sum(cost_usd) FROM llm_calls").fetchone()[0]
    # La somme du journal doit valoir la dépense réelle : un service par le
    # cache n'ajoute rien à la facture.
    assert total == pytest.approx(0.0004)


# --- tarification ---------------------------------------------------------


def test_cout_nominal():
    # 1M tokens d'entrée sur luna = 0,20 $
    assert estimate_cost("gpt-5.6-luna", 1_000_000, 0, 0) == pytest.approx(0.20)
    assert estimate_cost("gpt-5.6-luna", 0, 0, 1_000_000) == pytest.approx(1.20)


def test_les_tokens_caches_ne_sont_pas_factures_au_plein_tarif():
    """`usage.input_tokens` d'OpenAI inclut déjà les tokens cachés."""
    plein = estimate_cost("gpt-5.6-luna", 1_000_000, 0, 0)
    remise = estimate_cost("gpt-5.6-luna", 1_000_000, 1_000_000, 0)
    assert remise == pytest.approx(0.02)
    assert remise < plein


def test_modele_inconnu_leve_plutot_que_de_renvoyer_zero():
    with pytest.raises(UnknownModelPricing):
        estimate_cost("gpt-inexistant", 10, 0, 10)
