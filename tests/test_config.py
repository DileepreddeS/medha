from medha.config import Settings


def test_defaults(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)  # isolate from any real .env
    s = Settings()
    assert s.GLIMMER_MODEL_ID == "meta-models/Muse-Glimmer-30B"
    assert s.GLIMMER_SERVED_MODEL == "glimmer"
    assert s.META_BASE_URL == "https://api.meta.ai/v1"
    assert s.META_MODEL_ID == "muse-spark-1.3"
    assert s.ROUTE_MIN == 0.6
    assert s.HOLDOUT_MIN == 0.8
    assert s.ALLOW_ESCALATION is False
    assert s.DEMO_MODE is False
    # Glimmer is the default author since it went live (DECISIONS D2);
    # judge/escalation stay on meta (ROLE_PROVIDER).
    assert s.LLM_AUTHOR_PROVIDER == "glimmer"


def test_env_override(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)  # isolate from any real .env
    monkeypatch.setenv("GLIMMER_BASE_URL", "http://x/v1")
    monkeypatch.setenv("DAILY_SPEND_CAP_USD", "12.5")
    monkeypatch.setenv("LLM_AUTHOR_PROVIDER", "glimmer")
    s = Settings()
    assert s.GLIMMER_BASE_URL == "http://x/v1"
    assert s.DAILY_SPEND_CAP_USD == 12.5
    assert s.LLM_AUTHOR_PROVIDER == "glimmer"


def test_fixture_isolation(settings):
    assert settings.MEDHA_DB_PATH.endswith("medha.db")
    assert "medha_data" not in settings.MEDHA_DB_PATH
