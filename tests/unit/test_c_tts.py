from app.services.c_tts import stable_preset, to_c_markup


def test_markup_is_converted_to_native_engine_tags():
    text = to_c_markup("(tense:0.9) Halt! (pause) Was war das? (sigh)", pause_ms=420)
    assert text == "[dramatic] Halt! [pause:420ms] Was war das? [sigh]"


def test_preset_assignment_is_stable_and_gendered():
    assert stable_preset("Erzählerin", female=True) == stable_preset("Erzählerin", female=True)
    assert stable_preset("Erzählerin", female=True) in {"vivian", "serena", "sohee", "ono_anna"}
    assert stable_preset("Erzähler", female=False) in {"ryan", "aiden", "eric", "dylan", "uncle_fu"}
