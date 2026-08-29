"""Tests du harnais d'évaluation.

Comme au jalon 1 : aucun test ne sort sur le réseau. Le grader est pur, et le
runner tourne contre un double de fournisseur.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

import pytest
import yaml
from pydantic import BaseModel

from eval.grader import KINDS, grade, normalize
from eval.report import (
    CATEGORY_LABELS,
    QuestionsDiverge,
    render_comparison,
    render_markdown,
    summarize,
)
from eval.runner import (
    DEFAULT_PERSONAS,
    QUESTIONS_PATH,
    SUITES,
    execute,
    load_questions,
    questions_digest,
)
from src.api.schemas import RoutePlan
from src.llm.provider import LLMResponse, Message

# --- le grader ------------------------------------------------------------


@pytest.mark.parametrize(
    ("brut", "attendu"),
    [
        ("Raichu d'Alola", " raichu alola "),
        ("raichu-alola", " raichu alola "),
        ("RAICHU D’ALOLA", " raichu alola "),
        ("Épine-de-Fer", " epine de fer "),
    ],
)
def test_normalize_fait_converger_les_graphies(brut: str, attendu: str):
    assert normalize(brut) == attendu


def test_un_prefixe_ne_valide_pas_un_nom():
    """« rat » ne doit pas valider sur « Rattatac » : sans frontières de mot,
    le grader se féliciterait de réponses fausses."""
    assert not grade("Rattatac", {"kind": "names", "all_of": [["rat"]]}).ok
    assert grade("Un Rattata.", {"kind": "names", "all_of": [["rattata"]]}).ok


def test_names_accepte_indifferemment_le_francais_et_l_anglais():
    check = {"kind": "names", "all_of": [["hastacuda", "barraskewda"]]}
    assert grade("Hastacuda file à 136.", check).ok
    assert grade("Barraskewda tops the list.", check).ok


def test_none_of_detecte_un_nom_hallucine():
    """Trois noms justes et deux inventés font une réponse fausse, pas à moitié juste."""
    check = {
        "kind": "names",
        "all_of": [["hastacuda"]],
        "none_of": [["amphinobi", "greninja"]],
    }
    assert grade("Hastacuda.", check).ok
    verdict = grade("Hastacuda et Amphinobi.", check)
    assert not verdict.ok
    assert "amphinobi" in verdict.detail


def test_numbers_all_detecte_une_stat_manquante():
    check = {"kind": "numbers_all", "values": [78, 84, 109]}
    assert grade("78 / 84 / 78 / 109 / 85 / 100", check).ok
    verdict = grade("78 et 84, mais j'oublie le reste", check)
    assert not verdict.ok
    assert "109" in verdict.detail


def test_number_compare_des_valeurs_pas_des_sous_chaines():
    """« 1500 » contient « 150 » : c'est bien 150 qui est attendu, pas sa graphie."""
    assert grade("Le numéro est 150.", {"kind": "number", "value": 150}).ok
    assert not grade("Le numéro est 1500.", {"kind": "number", "value": 150}).ok
    assert grade("Environ 152.", {"kind": "number", "value": 150, "tolerance": 3}).ok


def test_un_kind_inconnu_est_une_erreur_pas_un_echec_silencieux():
    with pytest.raises(ValueError, match="kind"):
        grade("peu importe", {"kind": "vibes"})


# --- le jeu de questions --------------------------------------------------

REPARTITION = {"factuel": 15, "agregation": 10, "illustrateur": 10, "piege": 5}

# L'empreinte des 40 questions du jalon 2, publiée avec le 53,8 % puis avec le
# 92,5 %. Elle est gelée : si elle bouge, les deux chiffres cessent d'être
# comparables et tout ce que le README affirme s'écroule. La retenir ici la
# rend mécanique plutôt que déclarative.
EMPREINTE_GELEE = "b219b76777ad48f5"

TAILLES = {"principal": 40, "lore": 15, "multi": 10}


def test_l_empreinte_des_quarante_questions_n_a_pas_bouge():
    assert questions_digest(QUESTIONS_PATH) == EMPREINTE_GELEE


def test_le_jeu_de_questions_respecte_la_repartition_du_plan():
    questions = load_questions()
    assert len(questions) == 40
    compte: dict[str, int] = {}
    for q in questions:
        compte[q["category"]] = compte.get(q["category"], 0) + 1
    assert compte == REPARTITION


@pytest.mark.parametrize("suite", sorted(SUITES))
def test_chaque_question_est_verifiable(suite):
    """Sans `source`, un tableau d'évaluation n'est qu'une affirmation."""
    for q in load_questions(SUITES[suite]):
        for champ in ("id", "category", "question", "expected", "source", "check"):
            assert q.get(champ), f"{q.get('id')} : champ « {champ} » manquant"
        assert q["check"]["kind"] in KINDS, f"{q['id']} : kind inconnu"


@pytest.mark.parametrize("suite", sorted(SUITES))
def test_les_identifiants_sont_uniques(suite):
    ids = [q["id"] for q in load_questions(SUITES[suite])]
    assert len(set(ids)) == len(ids)


@pytest.mark.parametrize("suite", sorted(SUITES))
def test_le_fichier_de_questions_est_du_yaml_valide(suite):
    assert isinstance(yaml.safe_load(SUITES[suite].read_text(encoding="utf-8")), list)


@pytest.mark.parametrize("suite", sorted(SUITES))
def test_chaque_suite_a_sa_taille_annoncee(suite):
    assert len(load_questions(SUITES[suite])) == TAILLES[suite]


@pytest.mark.parametrize("suite", sorted(SUITES))
def test_chaque_suite_declare_ses_personas(suite):
    """Une suite sans entrée ferait tomber `main()` sur un KeyError, au moment
    précis où l'on croyait lancer une heure de run."""
    assert DEFAULT_PERSONAS[suite]


@pytest.mark.parametrize("suite", sorted(SUITES))
def test_toute_categorie_est_rendue_par_le_rapport(suite):
    """Le piège discret du rapport : une catégorie inconnue est **sautée**, pas
    signalée. Une coquille dans `category` ferait disparaître des questions du
    tableau sans que rien ne le dise."""
    for q in load_questions(SUITES[suite]):
        assert q["category"] in CATEGORY_LABELS, f"{q['id']} : catégorie non rendue"


# --- le runner ------------------------------------------------------------


class ScriptedProvider:
    """Répond toujours la même chose, et compte les étiquettes reçues."""

    name = "fake"
    model = "gpt-5.6-luna"

    def __init__(self, answer: str = "Hastacuda.", *, parsed: bool = True) -> None:
        self._answer = answer
        self._parsed = parsed
        self.labels: list[str | None] = []
        # Le harnais tape sur la vraie route : depuis le jalon 4 elle commence
        # par router. Ce double ne choisit aucun outil, ce qui fait replier
        # `/ask` sur la réponse de mémoire — sans base ni corpus, c'est le seul
        # chemin exécutable hors ligne.
        self.route_calls = 0

    def complete(
        self,
        messages: Sequence[Message],
        *,
        schema: type[BaseModel] | None = None,
        run_label: str | None = None,
    ) -> LLMResponse:
        self.labels.append(run_label)
        parsed = None
        if schema is RoutePlan:
            self.route_calls += 1
            parsed = RoutePlan(needs_db=False, lore_query=None, species=None, reason="hors ligne")
        elif self._parsed and schema is not None:
            parsed = schema.model_validate({"answer": self._answer, "confidence": 0.75})
        return LLMResponse(
            text=self._answer,
            model=self.model,
            input_tokens=120,
            cached_input_tokens=0,
            output_tokens=42,
            cost_usd=0.00007,
            latency_ms=350,
            cache_hit=False,
            parsed=parsed,
        )


QUESTIONS_JOUET = [
    {
        "id": "juste",
        "category": "agregation",
        "question": "Quels Pokémon Eau dépassent 130 en Vitesse ?",
        "expected": "Hastacuda",
        "source": "sql: ...",
        "check": {"kind": "names", "all_of": [["hastacuda", "barraskewda"]]},
    },
    {
        "id": "faux",
        "category": "factuel",
        "question": "Quel est le numéro national de Mewtwo ?",
        "expected": "150",
        "source": "sql: ...",
        "check": {"kind": "number", "value": 150},
    },
]


@pytest.fixture
def questions_jouet(tmp_path: Path) -> Path:
    path = tmp_path / "questions.yaml"
    path.write_text(yaml.safe_dump(QUESTIONS_JOUET, allow_unicode=True), encoding="utf-8")
    return path


def test_le_runner_note_les_reponses_et_ecrit_son_artefact(questions_jouet, tmp_path):
    payload, out = execute(
        personas=["factual"],
        questions_path=questions_jouet,
        provider=ScriptedProvider(),
        runs_dir=tmp_path / "runs",
        label="test-run",
    )

    assert out.exists()
    verdicts = {r["id"]: r["ok"] for r in payload["results"]}
    assert verdicts == {"juste": True, "faux": False}
    assert payload["run"]["model"] == "gpt-5.6-luna"
    assert payload["run"]["questions_sha256"]


def test_un_modele_prefixe_ne_casse_pas_le_nom_de_fichier(questions_jouet, tmp_path):
    """L'étiquette contient le nom du modèle, et une passerelle le préfixe par
    l'éditeur : `openai/gpt-oss-120b` viserait un sous-dossier inexistant."""
    payload, out = execute(
        personas=["factual"],
        questions_path=questions_jouet,
        provider=ScriptedProvider(),
        runs_dir=tmp_path / "runs",
        label="test-run-openai/gpt-oss-120b",
    )

    assert out.parent == tmp_path / "runs"
    assert out.name == "test-run-openai-gpt-oss-120b.json"
    # L'étiquette d'origine survit dans l'artefact : c'est elle qui relie le run
    # aux lignes de `llm_calls`.
    assert payload["run"]["label"] == "test-run-openai/gpt-oss-120b"


def test_le_run_label_atteint_le_fournisseur(questions_jouet, tmp_path):
    """C'est ce qui rend les lignes de `llm_calls` attribuables à un run."""
    provider = ScriptedProvider()
    execute(
        personas=["pokedex"],
        questions_path=questions_jouet,
        provider=provider,
        runs_dir=tmp_path / "runs",
        label="test-run",
    )
    # Deux appels par question sur ce chemin : le routeur, puis la réponse.
    # Tous doivent porter l'étiquette, sinon une part du coût d'un run n'est
    # plus attribuable dans le grand livre.
    assert provider.labels == ["test-run-pokedex"] * (2 * len(QUESTIONS_JOUET))
    assert provider.route_calls == len(QUESTIONS_JOUET)


def test_une_question_en_erreur_ne_fait_pas_tomber_le_run(questions_jouet, tmp_path):
    """Perdre 79 réponses payées parce que la 80e a pris un 502 serait absurde."""
    payload, out = execute(
        personas=["factual"],
        questions_path=questions_jouet,
        # parsed=False : la route répond 502, comme face à un modèle hors-schéma.
        provider=ScriptedProvider(parsed=False),
        runs_dir=tmp_path / "runs",
        label="test-run",
    )
    assert out.exists()
    assert len(payload["results"]) == len(QUESTIONS_JOUET)
    assert all(r["ok"] is False for r in payload["results"])
    assert all("502" in r["error"] for r in payload["results"])


def test_le_runner_respecte_limit(questions_jouet, tmp_path):
    payload, _ = execute(
        personas=["factual"],
        questions_path=questions_jouet,
        provider=ScriptedProvider(),
        runs_dir=tmp_path / "runs",
        label="test-run",
        limit=1,
    )
    assert len(payload["results"]) == 1


# --- le rapport -----------------------------------------------------------


def test_le_rapport_ignore_le_cache_dans_la_latence(questions_jouet, tmp_path):
    """La latence d'un appel servi par le cache ne mesure rien."""
    payload, _ = execute(
        personas=["factual"],
        questions_path=questions_jouet,
        provider=ScriptedProvider(),
        runs_dir=tmp_path / "runs",
        label="test-run",
    )
    payload["results"][0]["cache_hit"] = True
    payload["results"][0]["latency_ms"] = 1
    # 700 = 2 × 350 : la latence d'une question est celle du pipeline entier,
    # plan SQL compris. Ne compter que le second appel flatterait le chiffre.
    assert summarize(payload)["latency_p50"] == 700


def test_le_rapport_publie_exactitude_categories_et_calibration(questions_jouet, tmp_path):
    payload, _ = execute(
        personas=["factual"],
        questions_path=questions_jouet,
        provider=ScriptedProvider(),
        runs_dir=tmp_path / "runs",
        label="test-run",
    )
    markdown = render_markdown(payload)
    assert "Par catégorie" in markdown
    assert "Calibration" in markdown
    assert "1/2 (50,0 %)" in markdown


# --- l'avant / après ------------------------------------------------------


def _run(tmp_path, questions_jouet, *, answer: str, label: str) -> dict:
    payload, _ = execute(
        personas=["factual"],
        questions_path=questions_jouet,
        provider=ScriptedProvider(answer),
        runs_dir=tmp_path / "runs",
        label=label,
    )
    return payload


def test_l_avant_apres_chiffre_l_ecart_par_categorie(questions_jouet, tmp_path):
    avant = _run(tmp_path, questions_jouet, answer="Hastacuda.", label="avant")
    apres = _run(tmp_path, questions_jouet, answer="Hastacuda. 150.", label="apres")

    markdown = render_comparison(apres, avant, nom_avant="LLM nu", nom_apres="+ SQL")
    assert "LLM nu" in markdown and "+ SQL" in markdown
    # Le jeu jouet a deux questions : la seconde ne passe qu'avec « 150 ».
    assert "1/2 (50,0 %)" in markdown
    assert "2/2 (100,0 %)" in markdown
    assert "+50,0 pts" in markdown


def test_comparer_deux_jeux_de_questions_differents_est_refuse(questions_jouet, tmp_path):
    """C'est la façon la plus simple de fabriquer une progression flatteuse.

    L'empreinte voyage dans l'artefact précisément pour la rendre impossible à
    commettre en silence.
    """
    avant = _run(tmp_path, questions_jouet, answer="Hastacuda.", label="avant")
    apres = _run(tmp_path, questions_jouet, answer="Hastacuda.", label="apres")
    apres["run"]["questions_sha256"] = "0000000000000000"

    with pytest.raises(QuestionsDiverge, match="mêmes questions"):
        render_comparison(apres, avant)


def test_l_avant_apres_signale_un_run_servi_par_le_cache(questions_jouet, tmp_path):
    """Un run de reprise est massivement servi par le cache : son coût et sa
    latence ne mesurent rien, et rien n'empêcherait de coller un « 0,00000 $ »
    dans un README. Le rapport doit le dire lui-même."""
    avant = _run(tmp_path, questions_jouet, answer="Hastacuda.", label="avant")
    apres = _run(tmp_path, questions_jouet, answer="Hastacuda.", label="apres")
    for r in apres["results"]:
        r["cache_hit"] = True

    markdown = render_comparison(apres, avant, nom_apres="+ SQL")
    assert "servi à 2/2 par le cache" in markdown
    assert "L'exactitude, elle, est inchangée" in markdown
