"""Stage 2: the rule filter.

Fast, free checks that run before any AI model sees a job. Each rule reads
the job ad and the person's profile.yaml and either finds a clear blocker
("5+ years required", "German C1", "Head of Data") or stays quiet.

Two principles:
- Only drop on a clear, stated requirement. "Experience is a plus" or
  "German would be nice" is never a reason to drop; it becomes a note,
  and the AI ranker in stage 3 weighs it.
- Every drop carries the sentence that caused it, so you can always check
  the filter's work and spot a rule that got it wrong.

Everything here is a pure function: text and profile in, decision out.
No database, no network. That makes it instant and easy to test.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Literal

from roleradar.profiles import CEFR_LEVELS, ProfileConfig, cefr_rank

Decision = Literal["pass", "drop"]


@dataclass
class Finding:
    rule: str        # years, language, ad_language, seniority, lane, location
    message: str     # plain-language reason, shown on the dashboard
    evidence: str    # the sentence from the ad that triggered it


@dataclass
class FilterResult:
    decision: Decision
    reasons: list[Finding] = field(default_factory=list)  # why it was dropped
    notes: list[Finding] = field(default_factory=list)    # worth knowing, not a blocker

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class JobText:
    title: str
    company: str = ""
    location: str | None = None
    description: str | None = None


# --- Helpers ----------------------------------------------------------------------

# A requirement softened by any of these words is a wish, not a rule.
SOFT = re.compile(
    r"\b(?:prefer(?:red|ably)?|nice[- ]to[- ]have|a plus|is a bonus|an advantage|advantageous|"
    r"beneficial|ideally|desirable|optional|would be great|von vorteil|wünschenswert|"
    r"idealerweise|wäre schön|ein plus|gerne gesehen|kein muss|nicht zwingend)\b",
    re.IGNORECASE,
)

# A sentence about the company, not the candidate: "We have 20 years of experience".
COMPANY_VOICE = re.compile(r"\b(?:we|our|wir|unser\w*|since|seit|founded|gegründet)\b", re.IGNORECASE)

WORD_NUMBERS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
    "eight": 8, "nine": 9, "ten": 10,
    "ein": 1, "einem": 1, "einen": 1, "eins": 1, "zwei": 2, "drei": 3, "vier": 4,
    "fünf": 5, "sechs": 6, "sieben": 7, "acht": 8, "neun": 9, "zehn": 10,
}
_NUM = r"(\d{1,2}|" + "|".join(sorted(WORD_NUMBERS, key=len, reverse=True)) + r")"


def _to_int(token: str) -> int:
    return int(token) if token.isdigit() else WORD_NUMBERS[token.lower()]


def sentences(text: str) -> list[str]:
    """Split an ad into sentences and bullet points."""
    parts = re.split(r"(?<=[.!?;])\s+|\n+|•|·|•", text)
    return [p.strip() for p in parts if p and p.strip()]


def _clip(text: str, limit: int = 180) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _word_in(phrase: str, text: str) -> bool:
    return re.search(rf"(?<!\w){re.escape(phrase)}(?!\w)", text, re.IGNORECASE) is not None


# --- Rule 1: years of experience ---------------------------------------------------------

_YEARS_PATTERNS = [
    # "5+ years of professional experience", "3-5 years' experience", "two years in"
    re.compile(
        rf"{_NUM}\s*(?:\+|plus)?\s*(?:(?:-|–|to)\s*{_NUM}\s*\+?\s*)?years?['’]?\s+"
        r"(?:of\s+)?(?:[\w/+#.-]+\s+){0,5}?(?:experience|expertise|work|working|industry|professional)",
        re.IGNORECASE,
    ),
    # "experience: 3+ years", "experience of at least 4 years"
    re.compile(rf"experience[^.\n]{{0,40}}?{_NUM}\s*\+?\s*(?:(?:-|–|to)\s*{_NUM}\s*)?years?", re.IGNORECASE),
    # "mindestens 3 Jahre Berufserfahrung", "2-3 Jahre Erfahrung", "drei Jahren einschlägiger Erfahrung"
    re.compile(
        rf"{_NUM}\s*\+?\s*(?:(?:-|–|bis)\s*{_NUM}\s*)?jahre?n?\s+(?:[\w-]+\s+){{0,4}}?"
        r"(?:berufserfahrung|erfahrung|praxiserfahrung|berufspraxis)",
        re.IGNORECASE,
    ),
    # "Berufserfahrung von mindestens 3 Jahren"
    re.compile(
        rf"(?:berufs)?erfahrung[^.\n]{{0,40}}?{_NUM}\s*\+?\s*(?:(?:-|–|bis)\s*{_NUM}\s*)?jahre?n?",
        re.IGNORECASE,
    ),
]


@dataclass
class YearsRequirement:
    years: int
    soft: bool
    sentence: str


def find_years(description: str) -> list[YearsRequirement]:
    found = []
    for sentence in sentences(description):
        for pattern in _YEARS_PATTERNS:
            match = pattern.search(sentence)
            if not match:
                continue
            if COMPANY_VOICE.search(sentence[: match.start()]):
                break  # "We have 20 years of experience ..." is about them, not you
            low = _to_int(match.group(1))  # in a range "3-5 years", the bar is 3
            if 1 <= low <= 25:
                found.append(YearsRequirement(low, bool(SOFT.search(sentence)), sentence))
            break  # one requirement per sentence is enough
    return found


def check_years(job: JobText, profile: ProfileConfig) -> tuple[list[Finding], list[Finding]]:
    reasons, notes = [], []
    limit = profile.experience.drop_at_years
    for req in find_years(job.description or ""):
        if req.years >= limit and not req.soft:
            reasons.append(Finding(
                "years", f"Asks for {req.years}+ years of experience (you drop at {limit})", _clip(req.sentence)))
        elif req.years > profile.experience.years:
            kind = "Prefers" if req.soft else "Asks for"
            notes.append(Finding("years", f"{kind} {req.years} years of experience", _clip(req.sentence)))
    return reasons, notes


# --- Rule 2: language requirements ----------------------------------------------------------

LANGUAGE_WORDS = {
    "german": "German", "deutsch": "German", "deutschkenntnisse": "German", "deutsche": "German",
    "english": "English", "englisch": "English", "englischkenntnisse": "English",
    "french": "French", "französisch": "French", "französischkenntnisse": "French",
    "dutch": "Dutch", "niederländisch": "Dutch",
    "spanish": "Spanish", "spanisch": "Spanish",
    "italian": "Italian", "italienisch": "Italian",
    "polish": "Polish", "polnisch": "Polish",
    "arabic": "Arabic", "arabisch": "Arabic",
}
_LANGUAGE = re.compile(r"\b(" + "|".join(sorted(LANGUAGE_WORDS, key=len, reverse=True)) + r")\b", re.IGNORECASE)

# Words that describe a level, checked longest first ("sehr gut" before "gut").
LEVEL_WORDS: list[tuple[str, str]] = [
    (r"native|mother tongue|muttersprach\w*|first language", "native"),
    (r"business[- ]fluent|verhandlungssicher\w*|fluent(?:ly)?|fließend\w*|fliessend\w*|"
     r"excellent|exzellente?|sehr gute?n?|proficient|full professional|"
     r"ausgezeichnete?n?|hervorragende?n?|perfect|perfekte?n?", "C1"),
    (r"good|gute?n?|solide?n?|strong|professional working|working proficiency|"
     r"confident|sicher(?:e|en)?", "B2"),
    (r"intermediate|conversational|fortgeschrittene?n?", "B1"),
    (r"basic|grundkenntnisse|grundlegende?n?|elementary|beginner", "A2"),
]
_CEFR = re.compile(r"\b([ABC][12])\b(?:\s*[/-]\s*([ABC][12])\b)?")
_LEVEL_WORD = [(re.compile(rf"\b(?:{words})\b", re.IGNORECASE), level) for words, level in LEVEL_WORDS]


@dataclass
class LanguageRequirement:
    language: str
    level: str | None  # None when the ad names the language without a level
    soft: bool
    sentence: str


def _nearest_level(sentence: str, position: int) -> str | None:
    """The level word or CEFR code closest to a language mention (within 50 characters)."""
    found: list[tuple[int, int, str]] = []  # (start, end, level)
    for match in _CEFR.finditer(sentence):
        # "B2/C1" means "B2 or better", so the lower level is the real bar.
        codes = [c for c in match.groups() if c]
        found.append((match.start(), match.end(), min(codes, key=cefr_rank)))
    for pattern, level in _LEVEL_WORD:
        for match in pattern.finditer(sentence):
            found.append((match.start(), match.end(), level))
    # "sehr gute" also contains "gute"; the longer phrase wins, so drop matches inside another.
    found = [f for f in found if not any(o != f and o[0] <= f[0] and f[1] <= o[1] for o in found)]
    close = [(abs(start - position), level) for start, _, level in found if abs(start - position) <= 50]
    return min(close)[1] if close else None


def find_languages(description: str) -> list[LanguageRequirement]:
    found: dict[str, LanguageRequirement] = {}
    for sentence in sentences(description):
        for match in _LANGUAGE.finditer(sentence):
            language = LANGUAGE_WORDS[match.group(1).lower()]
            level = _nearest_level(sentence, match.start())
            req = LanguageRequirement(language, level, bool(SOFT.search(sentence)), sentence)
            current = found.get(language)
            # Keep the strictest firm requirement per language.
            if current is None or _strictness(req) > _strictness(current):
                found[language] = req
    return list(found.values())


def _strictness(req: LanguageRequirement) -> int:
    if req.level is None:
        return -1
    return cefr_rank(req.level) + (0 if req.soft else 10)


# Common words that tell German text from English text.
# Words like "in" that exist in both languages are left out on purpose.
_DE_WORDS = set("und der die das mit für wir sie ist ein eine einen zu von auf bei den dem des nicht "
                "oder auch als sich werden unser unsere ihre deine dein du bist dich dir uns sind "
                "hast hat bieten im zum zur über nach wie oder sowie".split())
_EN_WORDS = set("and the with for we you is a an to of on at our your are be will this that "
                "or as have has who".split())


def ad_language(description: str) -> str | None:
    """'German' or 'English' when the ad is clearly written in one; None if too short or mixed."""
    words = re.findall(r"[a-zäöüß]+", description.lower())
    de = sum(w in _DE_WORDS for w in words)
    en = sum(w in _EN_WORDS for w in words)
    if de + en < 12:
        return None
    share = de / (de + en)
    # Above 70% German words, the job is done in German. English ads with a German
    # legal footer stay well below that, so they are not mistaken for German ads.
    if share >= 0.7:
        return "German"
    if share <= 0.3:
        return "English"
    return None


def check_languages(job: JobText, profile: ProfileConfig) -> tuple[list[Finding], list[Finding]]:
    reasons, notes = [], []
    description = job.description or ""

    for req in find_languages(description):
        have = profile.level_in(req.language)
        have_label = profile.languages.get(req.language, "none")
        if req.level is None:
            if have < 0 and not req.soft:
                notes.append(Finding("language", f"Mentions {req.language}, which is not in your profile",
                                     _clip(req.sentence)))
            continue
        if cefr_rank(req.level) <= have:
            continue
        message = f"{req.language} {req.level} {'preferred' if req.soft else 'required'}; you have {have_label}"
        (notes if req.soft else reasons).append(Finding("language", message, _clip(req.sentence)))

    written_in = ad_language(description)
    if written_in and profile.level_in(written_in) < cefr_rank("B2"):
        have_label = profile.languages.get(written_in, "none")
        reasons.append(Finding(
            "ad_language",
            f"The ad is written in {written_in}, so the job most likely is too; you have {have_label}",
            _clip(description[:160]),
        ))
    return reasons, notes


# --- Rule 3: seniority in the title ------------------------------------------------------------

JUNIOR_OPEN = re.compile(
    r"\b(?:junior|entry[- ]level|graduates?|new grads?|berufseinsteiger\w*|absolvent\w*|"
    r"werkstudent\w*|working student|trainee|career starters?)\b",
    re.IGNORECASE,
)


def check_seniority(job: JobText, profile: ProfileConfig) -> list[Finding]:
    for word in profile.roles.seniority_words:
        if _word_in(word, job.title):
            if JUNIOR_OPEN.search(job.title) or JUNIOR_OPEN.search(job.description or ""):
                return []  # e.g. "Lead Developer, open to graduates" stays in
            return [Finding("seniority", f"'{word}' in the title, and the ad does not mention juniors", job.title)]
    return []


# --- Rule 4: out of lane -------------------------------------------------------------------------


def check_lane(job: JobText, profile: ProfileConfig) -> list[Finding]:
    for area in profile.roles.out_of_lane:
        if _word_in(area, job.title):
            return [Finding("lane", f"'{area}' is on your out-of-lane list", job.title)]
    return []


# --- Rule 5: location --------------------------------------------------------------------------------


def check_location(job: JobText, profile: ProfileConfig) -> list[Finding]:
    location = job.location or ""
    for place in profile.locations.exclude:
        if _word_in(place, location):
            return [Finding("location", f"'{place}' is on your excluded locations", location)]
    if not profile.locations.remote_ok and re.search(r"\bremote\b", location, re.IGNORECASE):
        return [Finding("location", "Remote job, and your profile says remote_ok: false", location)]
    return []


# --- All rules together ------------------------------------------------------------------------------


def evaluate(job: JobText, profile: ProfileConfig) -> FilterResult:
    reasons: list[Finding] = []
    notes: list[Finding] = []

    for check in (check_years, check_languages):
        r, n = check(job, profile)
        reasons += r
        notes += n
    reasons += check_seniority(job, profile)
    reasons += check_lane(job, profile)
    reasons += check_location(job, profile)

    if not (job.description or "").strip():
        notes.append(Finding("description", "No description yet, so only the title was checked", job.title))

    return FilterResult("drop" if reasons else "pass", reasons, notes)


__all__ = ["CEFR_LEVELS", "FilterResult", "Finding", "JobText", "evaluate",
           "find_languages", "find_years", "ad_language"]
