"""Fournisseur OpenAI, via l'API Responses.

Deux particularités de l'API valent d'être lues avant de toucher ce fichier :

1. **`temperature` n'existe plus sur la famille GPT-5.** L'API répond
   ``400 — only the default (1) value is supported``. Le levier de
   reproductibilité est remplacé par le couple `reasoning.effort` /
   `text.verbosity`, épinglé par configuration, plus le cache disque.
   Ce fichier est le seul endroit du projet qui connaît cette bizarrerie.
2. **Les refus ne sont pas des exceptions** : ils arrivent comme un item de
   contenu de type ``refusal``. Les ignorer donnerait une réponse vide et
   silencieuse ; on les remonte explicitement.
3. **« Compatible OpenAI » ne veut pas dire compatible ici.** Ce fichier parle
   l'API Responses ; la plupart des passerelles ne servent que
   ``/chat/completions``. ``base_url`` n'a donc de sens que pour celles qui
   exposent ``/responses`` — Groq aujourd'hui.
"""

from __future__ import annotations

import os
import time
from collections.abc import Sequence
from typing import Any
from urllib.parse import urlparse

import openai
from openai import OpenAI
from pydantic import BaseModel
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential_jitter,
)

from src.llm.cache import LLMCache, cache_key
from src.llm.pricing import estimate_cost
from src.llm.provider import LLMResponse, Message

# Familles de modèles qui pilotent la génération par l'effort de raisonnement
# plutôt que par l'échantillonnage.
_REASONING_FAMILIES = ("gpt-5", "gpt-oss", "o1", "o3", "o4")

# Erreurs qui méritent une nouvelle tentative : surcharge, coupure réseau, 5xx.
# Un 400 ou un 401 sont des bugs de notre côté, on ne les rejoue pas.
_RETRYABLE = (
    openai.RateLimitError,
    openai.APITimeoutError,
    openai.APIConnectionError,
    openai.InternalServerError,
)


def uses_reasoning_controls(model: str) -> bool:
    # Les passerelles préfixent par l'éditeur — Groq sert « openai/gpt-oss-120b ».
    # Sans ce découpage, le modèle retomberait sur `temperature`, que la famille
    # gpt-oss accepte : la bascule serait silencieuse, donc invisible au tableau.
    return model.rsplit("/", 1)[-1].startswith(_REASONING_FAMILIES)


def _endpoint_name(base_url: str | None) -> str:
    """Nom du fournisseur réellement interrogé.

    Il part dans `/health`, dans la clé de cache et dans le grand livre.
    Annoncer « openai » pendant que les requêtes filent chez Groq rendrait la
    sonde de santé fausse, et laisserait deux endpoints servant le même nom de
    modèle se répondre l'un l'autre depuis le cache.
    """
    if not base_url:
        return "openai"
    return urlparse(base_url).hostname or base_url


class OpenAIProvider:
    def __init__(
        self,
        *,
        model: str,
        cache: LLMCache,
        base_url: str | None = None,
        reasoning_effort: str = "low",
        verbosity: str = "low",
        timeout_s: float = 60.0,
        max_retries: int = 5,
        retry_wait_initial: float = 1.0,
        http_client: Any | None = None,
    ) -> None:
        # Volontairement os.environ[...] et non .get(...) : une clé absente doit
        # faire échouer le démarrage, pas produire un 401 obscur à la première
        # requête utilisateur.
        api_key = os.environ["OPENAI_API_KEY"]

        # max_retries=0 : le SDK sait réessayer, mais on veut que la politique
        # de reprise soit explicite, lisible et testable. Une seule couche.
        # http_client injectable : les tests branchent un transport factice pour
        # vérifier ce qui part réellement sur le fil, sans clé ni réseau.
        self._client = OpenAI(
            api_key=api_key,
            base_url=base_url,
            timeout=timeout_s,
            max_retries=0,
            http_client=http_client,
        )

        self.name = _endpoint_name(base_url)
        self.model = model
        self._cache = cache
        self._reasoning_effort = reasoning_effort
        self._verbosity = verbosity
        self._max_retries = max_retries
        self._retry_wait_initial = retry_wait_initial

    # -- paramètres d'appel ------------------------------------------------

    def _sampling_params(self, *, structured: bool) -> dict[str, Any]:
        if uses_reasoning_controls(self.model):
            params: dict[str, Any] = {"reasoning": {"effort": self._reasoning_effort}}
            # `text_format` de responses.parse() alimente déjà `text.format` ;
            # passer un `text` à côté entrerait en conflit. La verbosité n'est
            # donc réglée que pour les appels non structurés.
            if not structured:
                params["text"] = {"verbosity": self._verbosity}
            return params
        return {"temperature": 0.0}

    # -- appel ------------------------------------------------------------

    def complete(
        self,
        messages: Sequence[Message],
        *,
        schema: type[BaseModel] | None = None,
        run_label: str | None = None,
    ) -> LLMResponse:
        params = self._sampling_params(structured=schema is not None)
        fingerprint = f"{schema.__module__}.{schema.__qualname__}" if schema else None
        key = cache_key(
            provider=self.name,
            model=self.model,
            messages=[dict(m) for m in messages],
            params=params,
            schema_fingerprint=fingerprint,
        )

        started = time.perf_counter()
        cached = self._cache.get(key)
        if cached is not None:
            latency_ms = int((time.perf_counter() - started) * 1000)
            return self._finish(
                payload=cached, schema=schema, key=key, cache_hit=True, latency_ms=latency_ms
            )

        payload = self._call_api(messages, schema=schema, params=params)
        latency_ms = int((time.perf_counter() - started) * 1000)
        self._cache.put(key, provider=self.name, model=self.model, payload=payload)
        return self._finish(
            payload=payload,
            schema=schema,
            key=key,
            cache_hit=False,
            latency_ms=latency_ms,
            run_label=run_label,
        )

    def _call_api(
        self,
        messages: Sequence[Message],
        *,
        schema: type[BaseModel] | None,
        params: dict[str, Any],
    ) -> dict[str, Any]:
        @retry(
            retry=retry_if_exception_type(_RETRYABLE),
            stop=stop_after_attempt(self._max_retries),
            wait=wait_exponential_jitter(initial=self._retry_wait_initial, max=30),
            reraise=True,
        )
        def _send() -> Any:
            payload = {"model": self.model, "input": [dict(m) for m in messages], **params}
            if schema is not None:
                return self._client.responses.parse(text_format=schema, **payload)
            return self._client.responses.create(**payload)

        resp = _send()
        parsed = getattr(resp, "output_parsed", None)
        usage = getattr(resp, "usage", None)

        return {
            "model": getattr(resp, "model", self.model),
            "text": getattr(resp, "output_text", "") or "",
            "parsed": parsed.model_dump(mode="json") if parsed is not None else None,
            "refusal": _extract_refusal(resp),
            "input_tokens": getattr(usage, "input_tokens", 0) or 0,
            "cached_input_tokens": _cached_tokens(usage),
            "output_tokens": getattr(usage, "output_tokens", 0) or 0,
        }

    def _finish(
        self,
        *,
        payload: dict[str, Any],
        schema: type[BaseModel] | None,
        key: str,
        cache_hit: bool,
        latency_ms: int,
        run_label: str | None = None,
    ) -> LLMResponse:
        model = payload.get("model", self.model)
        input_tokens = payload["input_tokens"]
        cached_input_tokens = payload["cached_input_tokens"]
        output_tokens = payload["output_tokens"]

        # Un service par le cache n'a rien coûté. On garde les tokens pour
        # mémoire mais le coût réel est nul : la somme de la colonne du journal
        # doit correspondre à la facture, pas à une estimation.
        cost = (
            0.0
            if cache_hit
            else estimate_cost(model, input_tokens, cached_input_tokens, output_tokens)
        )

        parsed = None
        if schema is not None and payload.get("parsed") is not None:
            parsed = schema.model_validate(payload["parsed"])

        self._cache.log_call(
            provider=self.name,
            model=model,
            key=key,
            cache_hit=cache_hit,
            input_tokens=input_tokens,
            cached_input_tokens=cached_input_tokens,
            output_tokens=output_tokens,
            cost_usd=cost,
            latency_ms=latency_ms,
            run_label=run_label,
        )

        return LLMResponse(
            text=payload["text"],
            model=model,
            input_tokens=input_tokens,
            cached_input_tokens=cached_input_tokens,
            output_tokens=output_tokens,
            cost_usd=cost,
            latency_ms=latency_ms,
            cache_hit=cache_hit,
            parsed=parsed,
            refusal=payload.get("refusal"),
        )


def _cached_tokens(usage: Any) -> int:
    details = getattr(usage, "input_tokens_details", None)
    return getattr(details, "cached_tokens", 0) or 0


def _extract_refusal(resp: Any) -> str | None:
    for item in getattr(resp, "output", None) or []:
        for content in getattr(item, "content", None) or []:
            if getattr(content, "type", None) == "refusal":
                return getattr(content, "refusal", None) or "refus sans motif"
    return None
