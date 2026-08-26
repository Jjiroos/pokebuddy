"""La promesse « le dépôt tourne sans les clés » est la nôtre, donc on la teste.

Se reposer sur « le SDK attrape ses erreurs » ferait dépendre une garantie du
dépôt d'une note de version d'un tiers. Ces tests vérifient notre filet à nous,
et notamment qu'il tient quand le SDK, lui, ne tient pas.
"""

from __future__ import annotations

import pytest

from src.config import Settings
from src.obs import tracing


@pytest.fixture(autouse=True)
def client_neuf():
    """`_client` est mis en cache par processus : sans ça, le premier test
    fixerait la décision pour tous les suivants."""
    tracing._client.cache_clear()
    yield
    tracing._client.cache_clear()


def _regler(monkeypatch, **champs) -> None:
    # Ce test doit dire la même chose sur une machine dont le .env — ou
    # l'environnement — porte de vraies clés Langfuse que sur une machine
    # vierge. `_env_file=None` neutralise le fichier ; les deux clés à None
    # neutralisent les variables, que pydantic lirait sinon par-dessus.
    cles = {"langfuse_public_key": None, "langfuse_secret_key": None, **champs}
    reglages = Settings(_env_file=None, **cles)
    monkeypatch.setattr(tracing, "get_settings", lambda: reglages)


# --- la garantie ----------------------------------------------------------


@pytest.mark.parametrize(
    "champs",
    [
        {},
        {"langfuse_public_key": "pk-lf-test"},
        {"langfuse_secret_key": "sk-lf-test"},
    ],
    ids=["aucune clé", "clé publique seule", "clé secrète seule"],
)
def test_sans_paire_de_cles_le_tracage_est_eteint(monkeypatch, champs):
    """Une paire incomplète est une configuration ratée, pas une demi-
    activation : la moitié d'une authentification n'authentifie rien."""
    _regler(monkeypatch, **champs)
    assert tracing.enabled() is False
    assert tracing._client() is None


def test_une_variable_vide_vaut_non_renseignee():
    """`LANGFUSE_PUBLIC_KEY= make eval` doit réellement éteindre le traçage.
    Une chaîne vide arrive comme une valeur, pas comme une absence."""
    reglages = Settings(_env_file=None, langfuse_public_key="", langfuse_secret_key="")
    assert reglages.langfuse_public_key is None
    assert reglages.langfuse_secret_key is None


def test_le_span_muet_absorbe_tout(monkeypatch):
    _regler(monkeypatch)
    with tracing.span("route", kind=tracing.KIND_GENERATION, input="x") as trace:
        assert trace is tracing.MUET
        # Les mêmes champs qu'un vrai span, et aucun ne doit lever.
        trace.update(output={"a": 1}, usage_details={"input": 12}, cost_details={"total": 0.1})


def test_le_corps_du_with_s_execute_quand_meme(monkeypatch):
    """Un traçage éteint ne doit pas court-circuiter ce qu'il observait."""
    _regler(monkeypatch)
    temoin = []
    with tracing.span("route"):
        temoin.append(1)
    assert temoin == [1]


def test_flush_et_run_context_ne_font_rien_sans_cles(monkeypatch):
    _regler(monkeypatch)
    tracing.flush()
    with tracing.run_context("20260826-lore"):
        pass


# --- le filet quand le SDK, lui, échoue -----------------------------------


def test_un_sdk_qui_explose_ne_casse_pas_l_appelant(monkeypatch):
    """Le cas qui justifie d'écrire notre propre garde plutôt que de faire
    confiance : clés présentes, SDK inutilisable."""
    _regler(monkeypatch, langfuse_public_key="pk-lf-test", langfuse_secret_key="sk-lf-test")
    assert tracing.enabled() is True

    import builtins

    vrai_import = builtins.__import__

    def import_qui_echoue(name, *args, **kwargs):
        if name == "langfuse":
            raise ImportError("langfuse absent de l'image")
        return vrai_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", import_qui_echoue)
    assert tracing._client() is None
    with tracing.span("route") as trace:
        assert trace is tracing.MUET


def test_un_span_qui_refuse_de_s_ouvrir_ne_casse_pas_l_appelant(monkeypatch):
    class ClientCasse:
        def start_as_current_observation(self, **_):
            raise RuntimeError("exportateur OTel indisponible")

    monkeypatch.setattr(tracing, "_client", lambda: ClientCasse())
    temoin = []
    with tracing.span("route") as trace:
        assert trace is tracing.MUET
        temoin.append(1)
    assert temoin == [1]


def test_une_erreur_du_corps_traverse_le_span(monkeypatch):
    """La limite du filet, et elle est volontaire : le `try` couvre la
    construction du span, pas ce qu'il observe. Avaler l'exception de
    l'appelant ferait d'un outil d'observation une cause de panne muette."""
    _regler(monkeypatch)
    with pytest.raises(ZeroDivisionError), tracing.span("route"):
        raise ZeroDivisionError("le corps a échoué")


# --- quand les clés sont là, on appelle bien le SDK ------------------------


def test_le_span_est_delegue_au_sdk_quand_le_tracage_est_actif(monkeypatch):
    """Sans ce test, « ne rien casser » serait satisfait par un traçage qui ne
    trace jamais rien."""
    vus = []

    class FauxSpan:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def update(self, **champs):
            vus.append(("update", champs))

    class FauxClient:
        def start_as_current_observation(self, **champs):
            vus.append(("ouverture", champs))
            return FauxSpan()

    monkeypatch.setattr(tracing, "_client", lambda: FauxClient())
    with tracing.span(
        "corpus", kind=tracing.KIND_RETRIEVER, input="Il avale des feux follets."
    ) as t:
        t.update(output=[])

    ouverture = dict(vus[0][1])
    assert ouverture["name"] == "corpus"
    assert ouverture["as_type"] == tracing.KIND_RETRIEVER
    assert ouverture["input"] == "Il avale des feux follets."
    assert vus[1] == ("update", {"output": []})
