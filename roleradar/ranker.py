"""Stage 3: the AI ranker.

Jobs that passed the rule filter are judged by a local model (Ollama). The
model answers one question: how well does this person fit this job, and why?
The hard facts (years, languages, seniority) were already handled by the
rules in stage 2, so the model only does what rules can't: judge fit.

Three choices keep it fast on a laptop CPU:
1. The model reads only the requirements part of the ad (about 2,500
   characters), not the whole thing with company history and benefits.
2. Every answer is cached. The same job, profile and model is never asked twice.
3. One job at a time, with a generous timeout, and a stop button that works.

The final score is the model's fit plus fixed bonuses from profile.yaml,
so you can see exactly where every point came from.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import threading
from dataclasses import asdict, dataclass

from pydantic import BaseModel, Field, ValidationError

from roleradar.db import connect, transaction
from roleradar.ollama import OllamaClient, OllamaError
from roleradar.profiles import Profile, ProfileConfig, load_profile
from roleradar.rules import sentences
from roleradar.runs import finish, progress, set_total, start_run
from roleradar.settings import Settings

# Change this when the prompt changes, so cached answers to the old prompt are not reused.
PROMPT_VERSION = 1
EXCERPT_CHARS = 2500

SYSTEM_PROMPT = """You judge how well a candidate fits a job ad. Be strict and realistic.
The candidate is early in their career. Judge the job's actual work and requirements,
not its title alone. Score fit from 0 to 100:
90-100 the candidate matches nearly every requirement
70-89  strong match with small gaps
50-69  plausible, with clear gaps
30-49  weak match
0-29   different field or clearly not a fit
junior_friendly is true only if someone with the candidate's experience could realistically be hired.
Base everything only on the text given. Answer in English."""

SCHEMA = {
    "type": "object",
    "properties": {
        "fit": {"type": "integer", "minimum": 0, "maximum": 100},
        "role_family": {"type": "string"},
        "junior_friendly": {"type": "boolean"},
        "matches": {"type": "array", "items": {"type": "string"}, "maxItems": 4},
        "gaps": {"type": "array", "items": {"type": "string"}, "maxItems": 4},
        "reason": {"type": "string"},
    },
    "required": ["fit", "role_family", "junior_friendly", "matches", "gaps", "reason"],
}

TITLE_BONUS = 8          # the title matches one of your target roles
NOT_JUNIOR_PENALTY = 10  # the model thinks the job is out of reach at your level


class Judgement(BaseModel):
    fit: int = Field(ge=0, le=100)
    role_family: str = Field(max_length=80)
    junior_friendly: bool
    matches: list[str] = Field(default_factory=list, max_length=6)
    gaps: list[str] = Field(default_factory=list, max_length=6)
    reason: str = Field(max_length=600)


# --- What the model reads -----------------------------------------------------------------

_REQUIREMENT_HINTS = re.compile(
    r"\b(?:require|requirement|must|should|you have|you bring|you are|your profile|qualification|"
    r"experience|skills?|knowledge|degree|familiar|proficien|ideally|plus|responsibilit|you will|"
    r"tasks?|what you|stack|python|machine learning|ml\b|ai\b|data|"
    r"anforderung|profil|du bringst|du hast|sie haben|kenntnisse|erfahrung|aufgaben|studium)",
    re.IGNORECASE,
)


def requirement_excerpt(description: str, limit: int = EXCERPT_CHARS) -> str:
    """The sentences of an ad that say what the job needs, up to `limit` characters.

    Company history, benefits and legal text rarely change the fit, but they
    can be half the ad, and every character costs the model time.
    """
    if len(description) <= limit:
        return description  # short ads are sent whole; there is nothing to save
    picked, size = [], 0
    for sentence in sentences(description):
        if _REQUIREMENT_HINTS.search(sentence):
            if size + len(sentence) > limit:
                break
            picked.append(sentence)
            size += len(sentence) + 1
    if size < 200:  # almost nothing matched (odd format): fall back to the start of the ad
        return description[:limit]
    return "\n".join(picked)


def _section(markdown: str, heading: str) -> str:
    match = re.search(rf"^##\s+{heading}\s*$(.*?)(?=^##\s|\Z)", markdown, re.IGNORECASE | re.MULTILINE | re.DOTALL)
    return match.group(1).strip() if match else ""


def candidate_summary(profile: Profile) -> str:
    """A short description of the person, built from their profile files."""
    c = profile.config
    lines = [
        f"Target roles: {', '.join(c.roles.target)}",
        f"Professional experience: {c.experience.years:g} years",
        "Languages: " + ", ".join(f"{name} {level}" for name, level in c.languages.items()),
    ]
    skills = _section(profile.identity_md, "Skills")
    if skills:
        lines.append("Skills:\n" + skills[:700])
    stacks = re.findall(r"^##\s+(.+?)\s*$.*?^-\s*Stack:\s*(.+?)$", profile.projects_md, re.MULTILINE | re.DOTALL)
    if stacks:
        lines.append("Projects: " + "; ".join(f"{name} ({stack})" for name, stack in stacks[:6]))
    return "\n".join(lines)


def build_prompt(profile: Profile, title: str, company: str, location: str | None, description: str) -> str:
    return (
        f"CANDIDATE\n{candidate_summary(profile)}\n\n"
        f"JOB\nTitle: {title}\nCompany: {company}\nLocation: {location or 'not stated'}\n"
        f"Requirements and tasks:\n{requirement_excerpt(description)}"
    )


# --- Score -----------------------------------------------------------------------------------------


@dataclass
class Score:
    total: int
    fit: int
    location_bonus: int
    location_label: str | None
    title_bonus: int
    junior_penalty: int


def _title_matches(title: str, targets: list[str]) -> bool:
    words = set(re.findall(r"[a-z0-9+#]+", title.lower()))
    return any(set(re.findall(r"[a-z0-9+#]+", t.lower())) <= words for t in targets)


def score(judgement: Judgement, config: ProfileConfig, title: str, location: str | None) -> Score:
    location_bonus, label = 0, None
    for tier in config.locations.priority:
        if any(re.search(rf"\b{re.escape(word)}\b", location or "", re.IGNORECASE) for word in tier.match):
            location_bonus, label = tier.bonus, tier.label
            break
    title_bonus = TITLE_BONUS if _title_matches(title, config.roles.target) else 0
    penalty = 0 if judgement.junior_friendly else NOT_JUNIOR_PENALTY
    total = judgement.fit + location_bonus + title_bonus - penalty
    return Score(total, judgement.fit, location_bonus, label, title_bonus, penalty)


# --- Cache -----------------------------------------------------------------------------------------


def cache_key(model: str, prompt: str) -> str:
    # The prompt holds both the person's summary and the job, so a changed
    # profile or a changed ad gets a new key and is asked again.
    return hashlib.sha256(f"{PROMPT_VERSION}|{model}|{prompt}".encode()).hexdigest()


def _cached(conn: sqlite3.Connection, key: str) -> Judgement | None:
    row = conn.execute("SELECT result FROM rank_cache WHERE key = ?", (key,)).fetchone()
    return Judgement.model_validate_json(row["result"]) if row else None


# --- Running over stored jobs --------------------------------------------------------------------


def jobs_to_rank(conn: sqlite3.Connection, profile: str, rerank: bool) -> list[sqlite3.Row]:
    where = "status = 'passed'"
    if rerank:
        where += " OR status = 'ranked' OR (status = 'dropped' AND rank_result IS NOT NULL)"
    return conn.execute(
        f"SELECT id, title, company, location, description FROM jobs WHERE profile = ? AND ({where}) ORDER BY id",
        (profile,),
    ).fetchall()


def rank_one(conn: sqlite3.Connection, client: OllamaClient, model: str, profile: Profile,
             job: sqlite3.Row) -> tuple[str, Score, Judgement, bool]:
    """Judge one job and save the result. Returns (status, score, judgement, from_cache)."""
    prompt = build_prompt(profile, job["title"], job["company"], job["location"], job["description"] or "")
    key = cache_key(model, prompt)
    judgement = _cached(conn, key)
    from_cache = judgement is not None
    if judgement is None:
        raw = client.chat_json(model, SYSTEM_PROMPT, prompt, SCHEMA)
        raw["fit"] = max(0, min(100, int(raw.get("fit", 0))))
        try:
            judgement = Judgement.model_validate(raw)
        except ValidationError as exc:
            raise OllamaError(f"Unusable answer from the model: {exc.errors()[0]['msg']}") from exc

    result = score(judgement, profile.config, job["title"], job["location"])
    weak = judgement.fit < profile.config.scoring.min_fit
    status = "dropped" if weak else "ranked"
    reason = f"Weak fit ({judgement.fit}/100): {judgement.reason}" if weak else None
    payload = {"judgement": judgement.model_dump(), "score": asdict(result), "model": model}

    with transaction(conn):
        if not from_cache:
            conn.execute("INSERT OR REPLACE INTO rank_cache (key, model, result) VALUES (?, ?, ?)",
                         (key, model, judgement.model_dump_json()))
        conn.execute(
            "UPDATE jobs SET status = ?, status_reason = ?, rank_score = ?, rank_result = ?, "
            "ranked_at = datetime('now'), updated_at = datetime('now') WHERE id = ?",
            (status, reason, result.total, json.dumps(payload), job["id"]),
        )
    return status, result, judgement, from_cache


def run_rank(settings: Settings, profile_name: str, stop: threading.Event, *,
             rerank: bool = False, client: OllamaClient | None = None, run_id: int | None = None) -> None:
    """Rank every waiting job. Runs in a background thread with its own connection."""
    conn = connect(settings.db_path)
    client = client or OllamaClient(settings.ollama_url, settings.ollama_timeout)
    model = settings.ollama_model
    profile = load_profile(settings.profiles_dir, profile_name)
    jobs = jobs_to_rank(conn, profile_name, rerank)
    if run_id is None:
        run_id = start_run(conn, profile_name, "rank", len(jobs), f"Ranking with {model}")
    else:
        set_total(conn, run_id, len(jobs))

    counts = {"ranked": 0, "dropped": 0, "cached": 0, "failed": 0}
    consecutive_failures = 0
    try:
        for done, job in enumerate(jobs):
            if stop.is_set():
                finish(conn, run_id, "stopped", f"Stopped after {done} of {len(jobs)} jobs",
                       profile=profile_name, kind="rank", data=counts)
                return
            progress(conn, run_id, done, f"Ranking {done + 1} of {len(jobs)}: {job['title']} at {job['company']}")
            try:
                status, _, _, from_cache = rank_one(conn, client, model, profile, job)
            except OllamaError as exc:
                counts["failed"] += 1
                consecutive_failures += 1
                if consecutive_failures >= 3:
                    raise OllamaError(f"Three jobs in a row failed. Last error: {exc}") from exc
                continue
            consecutive_failures = 0
            counts[status] += 1
            counts["cached"] += from_cache
        progress(conn, run_id, len(jobs))
        message = (f"Ranked {len(jobs)} jobs with {model}: {counts['ranked']} kept, "
                   f"{counts['dropped']} weak fit{'s' if counts['dropped'] != 1 else ''} dropped"
                   + (f", {counts['failed']} failed (still waiting)" if counts["failed"] else "")
                   + (f", {counts['cached']} from cache" if counts["cached"] else ""))
        finish(conn, run_id, "done", message, profile=profile_name, kind="rank", data=counts)
    except Exception as exc:
        finish(conn, run_id, "failed", f"Ranking failed: {exc}", profile=profile_name, kind="rank",
               level="error", data=counts)
    finally:
        conn.close()
