"""The rule filter, tested on realistic snippets from English and German job ads.

Each case says what a careful human reader would decide. The traps (company
history, "nice to have", German legal footers) matter as much as the hits:
a filter that drops good jobs is worse than one that lets a few bad ones through.
"""

import pytest

from roleradar.profiles import ProfileConfig
from roleradar.rules import JobText, ad_language, evaluate, find_languages, find_years

PROFILE = ProfileConfig.model_validate({
    "display_name": "Test",
    "languages": {"English": "C1", "German": "B1", "Arabic": "native"},
    "experience": {"years": 1, "drop_at_years": 3},
    "roles": {"target": ["AI Engineer"], "out_of_lane": ["Sales", "Recruiter"]},
    "locations": {"exclude": ["United States"]},
})


def decide(title="AI Engineer", description="", location="Berlin"):
    return evaluate(JobText(title, "Acme", location, description), PROFILE)


# --- Years ---------------------------------------------------------------------------------

@pytest.mark.parametrize("text, years", [
    ("5+ years of professional experience in machine learning", 5),
    ("At least 3 years of experience with Python", 3),
    ("3-5 years' experience building ML systems", 3),
    ("You bring two years of industry experience", 2),
    ("Experience: 4+ years", 4),
    ("Professional experience of at least 6 years in data science", 6),
    ("Mindestens 3 Jahre Berufserfahrung in der Softwareentwicklung", 3),
    ("2-3 Jahre Erfahrung im Bereich Machine Learning", 2),
    ("Berufserfahrung von mindestens drei Jahren", 3),
    ("Du hast fünf Jahre einschlägige Berufserfahrung", 5),
])
def test_years_are_found(text, years):
    assert [r.years for r in find_years(text)] == [years]


@pytest.mark.parametrize("text", [
    "We have over 20 years of experience in retail technology.",
    "Unser Unternehmen hat seit 30 Jahren Erfahrung im Maschinenbau.",
    "Founded 10 years ago, we work with 500 clients.",
    "You will gain experience across the ML lifecycle.",
    "Python 3 and experience with Docker.",
])
def test_years_traps_are_ignored(text):
    assert find_years(text) == []


def test_years_at_limit_drop():
    result = decide(description="Requirements: 3+ years of experience with Python.")
    assert result.decision == "drop" and result.reasons[0].rule == "years"
    assert "3+ years" in result.reasons[0].evidence


def test_years_below_limit_pass_with_note():
    result = decide(description="You have 2 years of experience with SQL.")
    assert result.decision == "pass"
    assert result.notes[0].message == "Asks for 2 years of experience"


def test_soft_years_never_drop():
    result = decide(description="Ideally 5 years of experience in a similar role.")
    assert result.decision == "pass" and result.notes[0].message.startswith("Prefers 5")


# --- Languages -------------------------------------------------------------------------------

@pytest.mark.parametrize("text, language, level", [
    ("Fluent German (C1) is required", "German", "C1"),
    ("Deutschkenntnisse auf B2/C1-Niveau", "German", "B2"),
    ("sehr gute Deutschkenntnisse in Wort und Schrift", "German", "C1"),
    ("gute Deutschkenntnisse", "German", "B2"),
    ("verhandlungssichere Deutschkenntnisse", "German", "C1"),
    ("German at native level", "German", "native"),
    ("Basic German is enough", "German", "A2"),
    ("Excellent written and spoken English", "English", "C1"),
])
def test_language_levels(text, language, level):
    reqs = {r.language: r.level for r in find_languages(text)}
    assert reqs[language] == level


def test_two_languages_in_one_sentence():
    reqs = {r.language: r for r in find_languages("Fluent English; basic German is a plus.")}
    assert reqs["English"].level == "C1" and not reqs["English"].soft
    assert reqs["German"].level == "A2" and reqs["German"].soft


@pytest.mark.parametrize("description, decision", [
    ("Fluent German (C1) and English required.", "drop"),
    ("Sehr gute Deutschkenntnisse setzen wir voraus.", "drop"),
    ("Good German skills are required.", "drop"),            # B2 > your B1
    ("Conversational German (B1) is fine.", "pass"),          # equal is fine
    ("German is a plus, English is a must.", "pass"),
    ("Fluent German would be nice to have.", "pass"),         # soft
    ("Excellent English skills required.", "pass"),           # C1 = your C1
    ("Native English speaker required.", "drop"),             # native > C1
    ("Fluent Arabic is a big advantage.", "pass"),
])
def test_language_decisions(description, decision):
    assert decide(description=description).decision == decision


def test_soft_language_becomes_a_note():
    result = decide(description="Fluent German is beneficial.")
    assert result.decision == "pass"
    assert result.notes[0].message == "German C1 preferred; you have B1"


def test_unknown_language_is_noted():
    result = decide(description="You speak French.")
    assert result.decision == "pass" and "French" in result.notes[0].message


# --- Language of the ad itself ----------------------------------------------------------------

GERMAN_AD = ("Wir suchen dich als KI-Entwickler! Du arbeitest mit unserem Team an spannenden "
             "Projekten und bist für die Entwicklung von Modellen verantwortlich. Wir bieten dir "
             "ein tolles Team, flexible Arbeitszeiten und die Möglichkeit, dich weiterzuentwickeln.")
ENGLISH_AD = ("We are looking for a Junior AI Engineer to join our team in Berlin. You will build "
              "and ship ML features with Python and work closely with our product team. You have "
              "a degree in computer science and you are curious about the latest research. ")
LEGAL_FOOTER = "Wir freuen uns auf deine Bewerbung. Die Stelle ist unbefristet und in Vollzeit."


def test_ad_language_detection():
    assert ad_language(GERMAN_AD) == "German"
    assert ad_language(ENGLISH_AD) == "English"
    assert ad_language(ENGLISH_AD + LEGAL_FOOTER) == "English"  # footer must not flip it
    assert ad_language("Python, SQL, Docker") is None              # too short to tell


def test_ad_in_german_drops_for_b1():
    result = decide(description=GERMAN_AD)
    assert result.decision == "drop" and result.reasons[0].rule == "ad_language"


def test_ad_in_german_passes_for_b2_speaker():
    profile = PROFILE.model_copy(update={"languages": {**PROFILE.languages, "German": "B2"}})
    assert evaluate(JobText("KI-Entwickler", "X", "Berlin", GERMAN_AD), profile).decision == "pass"


# --- Seniority, lane, location -----------------------------------------------------------------

@pytest.mark.parametrize("title, description, decision", [
    ("Staff Machine Learning Engineer", "Lead the ML platform.", "drop"),
    ("Head of Data", "", "drop"),
    ("Principal AI Architect", "", "drop"),
    ("Lead Data Engineer", "A great role for ambitious graduates.", "pass"),
    ("Tech Lead (Junior welcome)", "", "pass"),
    ("Senior AI Engineer", "", "pass"),   # "Senior" alone is left to the AI ranker by default
    ("Leading AI Engineer", "", "pass"),  # whole words only: "Leading" is not "Lead"
])
def test_seniority(title, description, decision):
    assert decide(title=title, description=description).decision == decision


def test_out_of_lane():
    result = decide(title="Sales Manager AI Solutions")
    assert result.decision == "drop" and result.reasons[0].rule == "lane"
    assert decide(title="Salesforce Developer").decision == "pass"  # whole words only


def test_excluded_location():
    assert decide(location="New York, United States").decision == "drop"
    assert decide(location="Berlin, Germany").decision == "pass"


def test_remote_not_ok():
    profile = PROFILE.model_copy(update={"locations": PROFILE.locations.model_copy(update={"remote_ok": False})})
    assert evaluate(JobText("AI Engineer", "X", "Remote, EMEA", ""), profile).decision == "drop"


# --- All together --------------------------------------------------------------------------------

def test_several_reasons_are_all_reported():
    result = decide(title="Head of AI", description="10+ years of experience. Fluent German (C1).")
    assert {f.rule for f in result.reasons} == {"years", "language", "seniority"}


def test_missing_description_passes_with_note():
    result = decide(description="")
    assert result.decision == "pass" and result.notes[-1].rule == "description"


def test_typical_good_junior_ad_passes_cleanly():
    ad = ("Junior AI Engineer (m/w/d). You have a degree in computer science and 0-2 years of "
          "experience with Python and PyTorch. Fluent English is required; German is a plus. "
          "Hybrid in Berlin.")
    result = decide(title="Junior AI Engineer (m/w/d)", description=ad)
    assert result.decision == "pass" and result.reasons == []
