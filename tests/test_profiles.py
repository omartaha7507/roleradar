import pytest

from roleradar.profiles import (
    ProfileError,
    ProfileNotFound,
    list_profile_names,
    load_profile,
)


def test_example_profile_loads(settings):
    profile = load_profile(settings.profiles_dir, "alex")
    assert profile.config.display_name == "Alex Example"
    assert profile.config.experience.drop_at_years == 3
    assert "Experience" in profile.identity_md
    assert profile.config.level_in("german") == profile.config.level_in("German") == 2  # B1
    assert profile.config.level_in("French") == -1


@pytest.mark.parametrize("bad_name", ["../etc", "Alex", "a" * 41, "", "a b"])
def test_unsafe_names_are_rejected(settings, bad_name):
    with pytest.raises(ProfileNotFound):
        load_profile(settings.profiles_dir, bad_name)


def test_missing_profile(settings):
    with pytest.raises(ProfileNotFound):
        load_profile(settings.profiles_dir, "nobody")


def test_typo_in_profile_is_reported(settings):
    path = settings.profiles_dir / "alex" / "profile.yaml"
    path.write_text(path.read_text().replace("drop_at_years", "drop_at_yaers"))
    with pytest.raises(ProfileError, match="drop_at_yaers"):
        load_profile(settings.profiles_dir, "alex")


def test_bad_language_level_is_reported(settings):
    path = settings.profiles_dir / "alex" / "profile.yaml"
    path.write_text(path.read_text().replace("German: B1", "German: fluent"))
    with pytest.raises(ProfileError, match="languages"):
        load_profile(settings.profiles_dir, "alex")


def test_list_ignores_folders_without_profile(settings):
    (settings.profiles_dir / "empty").mkdir()
    assert list_profile_names(settings.profiles_dir) == ["alex"]
