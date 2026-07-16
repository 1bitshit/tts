from app.services.story_pipeline import embed_text, rank_candidates


def test_embedding_is_deterministic_and_normalized():
    first = embed_text("Der Zug erreicht den Bahnhof im Regen.")
    second = embed_text("Der Zug erreicht den Bahnhof im Regen.")
    assert first == second
    assert abs(sum(value * value for value in first) - 1.0) < 1e-6


def test_ranking_penalizes_semantic_repetition():
    history = ["Der Zug erreicht den Bahnhof und bremst am Bahnsteig."]
    repeated = "(tense) Der Zug erreicht den Bahnhof und bremst am Bahnsteig."
    novel = "(tense) Mara entdeckt im verlassenen Wartesaal einen versiegelten Koffer."
    ranked = rank_candidates([repeated, novel], history, is_narrator=False)
    assert ranked[0].text == novel
    assert ranked[0].score > ranked[1].score
