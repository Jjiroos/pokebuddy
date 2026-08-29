"""L'outil de recherche dans le corpus, sans base ni modèle de plongement.

Ce qui se teste ici n'est pas la requête SQL — c'est le **plancher de
similarité**. Une recherche vectorielle rend toujours ses k plus proches
voisins : sans seuil, une question hors périmètre récolte cinq entrées de
Pokédex sans rapport, et un modèle à qui on les tend finit par en tirer
quelque chose. Savoir ne rien renvoyer est la moitié du travail.

`keep_close_enough` a été sorti de `search()` exactement pour ça : le
comportement intéressant se vérifie sans réseau et sans 220 Mo d'ONNX.
"""

from __future__ import annotations

from src.tools.rag import MAX_DISTANCE, LoreHit, _named, keep_close_enough

# (name_fr, name_en, version, texte, distance) — la forme que rend la requête.
PROCHE = ("Téraclope", "dusclops", "black", "Il avale des feux follets.", 0.12)
LIMITE = ("Ronflex", "snorlax", "moon", "Il mange 400 kg par jour.", MAX_DISTANCE)
LOIN = ("Pikachu", "pikachu", "red", "Il stocke de l'électricité.", 0.72)


def test_les_passages_trop_eloignes_sont_ecartes():
    assert keep_close_enough([PROCHE, LOIN], MAX_DISTANCE) == [
        LoreHit("Téraclope", "dusclops", "black", "Il avale des feux follets.", 0.12)
    ]


def test_une_question_hors_perimetre_ne_renvoie_rien():
    """Le cas qui justifie le seuil : cinq voisins existent toujours, aucun ne
    répond. Renvoyer la liste vide est la bonne réponse."""
    assert keep_close_enough([LOIN, LOIN, LOIN], MAX_DISTANCE) == []


def test_le_seuil_est_inclusif():
    """Une distance exactement au seuil passe. Le bord doit être décidé une
    fois pour toutes : sinon la calibration mesure une chose et le code une
    autre."""
    assert len(keep_close_enough([LIMITE], MAX_DISTANCE)) == 1


def test_le_nom_anglais_sert_de_repli_quand_le_francais_manque():
    """Toutes les espèces n'ont pas de `name_fr` en base. Un extrait sans nom
    affichable serait inutilisable par le modèle comme par le lecteur."""
    (hit,) = keep_close_enough([(None, "dusclops", "black", "…", 0.1)], MAX_DISTANCE)
    assert hit.species_fr == "dusclops"


def test_la_citation_est_une_cle_pas_une_etiquette():
    """`pokedex:<espèce>/<jeu>` doit retrouver l'entrée exacte en une requête —
    donc l'identifiant anglais, celui que la base porte, et pas le nom français."""
    (hit,) = keep_close_enough([PROCHE], MAX_DISTANCE)
    assert hit.citation == "pokedex:dusclops/black"


# --- le filtre par espèce -------------------------------------------------


def _sql(expression) -> str:
    return str(expression.compile(compile_kwargs={"literal_binds": True}))


def test_le_nom_d_espece_est_compare_en_egalite_pas_en_joker():
    """La valeur vient du modèle. Avec un `ILIKE`, un « % » égaré ramènerait la
    moitié du corpus sous l'étiquette d'une seule espèce — une source fausse et
    crédible, exactement ce que le projet cherche à éviter."""
    rendu = _sql(_named("%"))
    assert "LIKE" not in rendu.upper()
    # Le « % » est une valeur comparée, pas un motif.
    assert "lower(species.name_fr) = '%'" in rendu


def test_le_nom_est_cherche_en_francais_comme_en_anglais():
    """Le routeur rend ce que la question écrit, et les questions sont en
    français ; les entrées, elles, sont indexées sur l'identifiant anglais."""
    rendu = _sql(_named("  Téraclope  "))
    assert "species.name_fr" in rendu and "species.name" in rendu
    assert "'téraclope'" in rendu  # normalisé : espaces retirés, minuscules
