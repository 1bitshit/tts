from app.config import settings
from app.services.story_model_runtime import model_for_role


def test_story_roles_use_large_models():
    assert model_for_role("author") == settings.story_author_model
    assert "14B" in model_for_role("author")
    assert model_for_role("editor") == settings.story_editor_model
    assert "24B" in model_for_role("editor")


def test_unknown_story_role_is_rejected():
    try:
        model_for_role("random")
    except ValueError as exc:
        assert "Unknown story model role" in str(exc)
    else:
        raise AssertionError("unknown role was accepted")
