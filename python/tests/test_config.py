from app.config import create_config


def make_cfg(tmp_path, **overrides):
    return create_config(env_file=tmp_path / "none.env", overrides=overrides)


def test_stt_is_disabled_by_flag(tmp_path):
    cfg = make_cfg(tmp_path, STT_ENABLED="false", STT_PROVIDER="local")
    assert cfg.has.stt is False


def test_local_stt_is_available_without_key(tmp_path):
    cfg = make_cfg(tmp_path, STT_ENABLED="true", STT_PROVIDER="local", STT_API_KEY="")
    assert cfg.has.stt is True


def test_cloud_stt_requires_key(tmp_path):
    cfg = make_cfg(tmp_path, STT_ENABLED="true", STT_PROVIDER="openai", STT_API_KEY="")
    assert cfg.has.stt is False

    cfg2 = make_cfg(tmp_path, STT_ENABLED="true", STT_PROVIDER="openai", STT_API_KEY="sk-test")
    assert cfg2.has.stt is True

def test_sent_folder_is_excluded_by_default(tmp_path):
    cfg = make_cfg(tmp_path)
    assert cfg.mail.include_sent is False


def test_sent_folder_can_be_enabled(tmp_path):
    cfg = make_cfg(tmp_path, MAIL_INCLUDE_SENT="true")
    assert cfg.mail.include_sent is True
