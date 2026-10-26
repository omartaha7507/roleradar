"""Stage 5: writing a tailored CV and cover letter for the best jobs.

For each chosen job:
1. Live check: is the posting still open? Closed jobs are skipped.
2. Claude Code (run headless, `claude -p`) reads the job ad and the
   person's identity.md and projects.md, and returns the CV and letter
   content as JSON.
3. The JSON is checked: right shape (documents.Application) and honest
   (every employer, school and project must exist in the profile). If
   either check fails, Claude gets the problems and one more try.
4. RoleRadar lays out the HTML, prints PDFs with Chrome, and saves
   everything in data/applications/<date>/<company>_<title>_<id>/.

Claude writes the words; RoleRadar controls the facts and the layout.
"""

from __future__ import annotations

import json
import re
import shutil
import sqlite3
import subprocess
import threading
import unicodedata
from collections.abc import Callable
from datetime import date
from pathlib import Path

from pydantic import ValidationError

from roleradar import livecheck
from roleradar.db import connect, log_event, transaction
from roleradar.documents import Application, check_facts, clean_dashes, render_cv, render_letter
from roleradar.pdf import find_browser, html_to_pdf
from roleradar.profiles import Profile, cefr_rank, load_profile
from roleradar.rules import ad_language
from roleradar.runs import finish, progress, set_total, start_run
from roleradar.settings import Settings

Runner = Callable[[str, Path], str]          # (prompt, working folder) -> Claude's answer text
LiveCheck = Callable[[str], tuple[str, str]]  # url -> (status, explanation)


class BuildError(Exception):
    pass


class FatalBuildError(BuildError):
    """A problem no other job can avoid either (Claude Code missing or signed out)."""


# --- The prompt ---------------------------------------------------------------------------

INSTRUCTIONS = """You write a tailored CV and cover letter for one job application.

HARD RULES
- Use only facts written in IDENTITY and PROJECTS below. Never invent or
  inflate employers, job titles, dates, numbers, degrees, skills or links.
  You may rephrase, shorten, reorder and choose which facts to show.
- Copy contact details exactly as written in IDENTITY.
- Never use em dashes or en dashes. Use commas, colons or hyphens.
- Write in {language}.

CV (must fit on one A4 page)
- headline: max 90 characters, aimed at this job.
- summary: 2-3 sentences connecting the candidate's real experience to this job.
- skills: up to 5 groups, most relevant to the job first; only skills from the profile.
- experience: every job in IDENTITY, most recent first, 2-4 bullets each,
  leading with what matters for this job.
- projects: the 2-4 projects from PROJECTS that best match this job, 1-2 bullets each.
- education and languages as in IDENTITY.

COVER LETTER
- 3-4 short paragraphs, under 300 words, specific to this company and role.
- Mention 2 concrete facts from the profile that match the ad.
- greeting: "Dear <company name> team," unless the ad names a contact person.
- No clichés like "I am writing to express my interest".

FIT
- summary: 1-2 sentences on why this candidate fits.
- flags: requirements in the ad the profile does not show (e.g. "Asks for Kubernetes").

OUTPUT
Return ONLY one JSON object, no other text, with this shape:
{schema}
"""


def _schema_outline() -> str:
    return json.dumps(Application.model_json_schema(), separators=(",", ":"))


def letter_language(profile: Profile, description: str) -> tuple[str, str]:
    """German only when the ad is in German AND the person writes German at C1 or better."""
    if ad_language(description) == "German" and profile.config.level_in("German") >= cefr_rank("C1"):
        return "de", "German"
    return "en", "English"


def build_prompt(profile: Profile, job: sqlite3.Row) -> tuple[str, str]:
    code, language = letter_language(profile, job["description"] or "")
    prompt = (
        INSTRUCTIONS.format(language=language, schema=_schema_outline())
        + f"\n\nIDENTITY\n{profile.identity_md}\n\nPROJECTS\n{profile.projects_md}\n\n"
        f"JOB\nTitle: {job['title']}\nCompany: {job['company']}\nLocation: {job['location'] or 'not stated'}\n"
        f"{job['description'] or '(no description available)'}"
    )
    return code, prompt


# --- Talking to Claude Code ---------------------------------------------------------------------


def claude_runner(settings: Settings) -> Runner:
    """The real runner: `claude -p` with the prompt on standard input."""
    command = shutil.which(settings.claude_command)
    if not command:
        raise FatalBuildError(
            "Claude Code was not found. Install it (see the README), open a terminal, "
            "run 'claude' once and sign in, then try again."
        )

    def run(prompt: str, cwd: Path) -> str:
        try:
            done = subprocess.run(
                [command, "-p", "--output-format", "json", "--model", settings.claude_model,
                 "--permission-mode", "dontAsk"],
                input=prompt, capture_output=True, text=True, encoding="utf-8",
                cwd=cwd, timeout=settings.claude_timeout,
            )
        except subprocess.TimeoutExpired as exc:
            raise BuildError(f"Claude took longer than {settings.claude_timeout} seconds") from exc
        try:
            answer = json.loads(done.stdout)
        except json.JSONDecodeError:
            detail = (done.stderr or done.stdout).strip()[:300]
            raise BuildError(f"Claude Code failed: {detail or 'no output'}") from None
        if answer.get("is_error"):
            detail = str(answer.get("result"))[:300]
            if re.search(r"log ?in|auth|api key|credit|subscription", detail, re.IGNORECASE):
                raise FatalBuildError(f"Claude Code is not signed in or has no usage left: {detail}")
            raise BuildError(f"Claude Code reported a problem: {detail}")
        return answer.get("result") or ""

    return run


def parse_application(text: str) -> Application:
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end < start:
        raise BuildError("Claude's answer contained no JSON")
    try:
        data = json.loads(text[start:end + 1])
    except json.JSONDecodeError as exc:
        raise BuildError(f"Claude's JSON was broken: {exc}") from exc
    try:
        return Application.model_validate(clean_dashes(data))
    except ValidationError as exc:
        problems = "; ".join(f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors()[:6])
        raise BuildError(f"Claude's JSON did not match the CV format: {problems}") from exc


def write_application(profile: Profile, job: sqlite3.Row, runner: Runner, folder: Path) -> Application:
    """Ask Claude, check the answer, and ask once more with the problems if needed."""
    _, prompt = build_prompt(profile, job)
    problems: list[str] = []
    for attempt in (1, 2):
        text = runner(prompt if attempt == 1 else prompt + _retry_note(problems), folder)
        try:
            app = parse_application(text)
        except BuildError as exc:
            problems = [str(exc)]
            continue
        problems = check_facts(app, profile.identity_md, profile.projects_md)
        if not problems:
            return app
    raise BuildError("The documents failed the checks twice: " + "; ".join(problems))


def _retry_note(problems: list[str]) -> str:
    return ("\n\nYOUR PREVIOUS ANSWER WAS REJECTED:\n- " + "\n- ".join(problems)
            + "\nFix these and return the complete JSON object again. Use only facts from IDENTITY and PROJECTS.")


# --- Files ------------------------------------------------------------------------------------------


def slug(text: str, limit: int = 40) -> str:
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    text = re.sub(r"\((?:[mwfdx]/){2,}[mwfdx]\)", "", text, flags=re.IGNORECASE)
    return re.sub(r"[^A-Za-z0-9]+", "-", text).strip("-")[:limit].strip("-") or "job"


def save_documents(app: Application, job: sqlite3.Row, folder: Path, browser: str | None) -> dict[str, str | None]:
    folder.mkdir(parents=True, exist_ok=True)
    name = slug(app.cv.contact.name, 30)
    company = slug(job["company"], 30)
    files = {
        "cv_html": f"{name}_CV_{company}.html",
        "letter_html": f"{name}_Cover_Letter_{company}.html",
    }
    (folder / files["cv_html"]).write_text(render_cv(app), encoding="utf-8")
    (folder / files["letter_html"]).write_text(render_letter(app, job["company"], job["title"]), encoding="utf-8")
    (folder / "application.json").write_text(app.model_dump_json(indent=2), encoding="utf-8")
    (folder / "job.md").write_text(
        f"# {job['title']}\n\n{job['company']} · {job['location'] or ''}\n\n{job['url']}\n\n{job['description'] or ''}",
        encoding="utf-8",
    )
    result: dict[str, str | None] = {**files, "cv_pdf": None, "letter_pdf": None}
    for kind in ("cv", "letter"):
        pdf_name = files[f"{kind}_html"].replace(".html", ".pdf")
        if html_to_pdf(folder / files[f"{kind}_html"], folder / pdf_name, browser):
            result[f"{kind}_pdf"] = pdf_name
    return result


# --- Choosing jobs and running ----------------------------------------------------------------------


def jobs_to_build(conn: sqlite3.Connection, profile: str, job_ids: list[int] | None, count: int | None) -> list[sqlite3.Row]:
    if job_ids:
        marks = ",".join("?" * len(job_ids))
        return conn.execute(
            f"SELECT * FROM jobs WHERE profile = ? AND id IN ({marks}) AND status IN ('ranked', 'built', 'passed') "
            "ORDER BY rank_score DESC NULLS LAST", (profile, *job_ids),
        ).fetchall()
    return conn.execute(
        "SELECT * FROM jobs WHERE profile = ? AND status = 'ranked' ORDER BY rank_score DESC NULLS LAST, id LIMIT ?",
        (profile, count or 5),
    ).fetchall()


def build_one(conn: sqlite3.Connection, settings: Settings, profile: Profile, job: sqlite3.Row,
              runner: Runner, check_live: LiveCheck, browser: str | None, model: str) -> str:
    """Build documents for one job. Returns 'built' or 'closed'."""
    status, why = check_live(job["url"])
    if status == "closed":
        with transaction(conn):
            conn.execute("UPDATE jobs SET status = 'closed', status_reason = ?, updated_at = datetime('now') "
                         "WHERE id = ?", (why, job["id"]))
        return "closed"

    folder = (settings.applications_dir / profile.name / date.today().isoformat()
              / f"{slug(job['company'], 30)}_{slug(job['title'])}_{job['id']}")
    folder.mkdir(parents=True, exist_ok=True)
    app = write_application(profile, job, runner, folder)
    files = save_documents(app, job, folder, browser)

    with transaction(conn):
        conn.execute(
            "INSERT INTO builds (profile, job_id, folder, cv_pdf, letter_pdf, language, fit_summary, flags, model) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (profile.name, job["id"], str(folder), files["cv_pdf"], files["letter_pdf"], app.language,
             app.fit.summary, json.dumps(app.fit.flags), model),
        )
        conn.execute("UPDATE jobs SET status = 'built', status_reason = NULL, updated_at = datetime('now') "
                     "WHERE id = ?", (job["id"],))
    return "built"


def run_build(settings: Settings, profile_name: str, stop: threading.Event, *, run_id: int,
              job_ids: list[int] | None = None, count: int | None = None,
              runner: Runner | None = None, check_live: LiveCheck | None = None) -> None:
    conn = connect(settings.db_path)
    counts = {"built": 0, "closed": 0, "failed": 0}
    try:
        profile = load_profile(settings.profiles_dir, profile_name)
        runner = runner or claude_runner(settings)
        check_live = check_live or livecheck.check
        browser = find_browser()
        jobs = jobs_to_build(conn, profile_name, job_ids, count)
        set_total(conn, run_id, len(jobs))
        for done, job in enumerate(jobs):
            if stop.is_set():
                finish(conn, run_id, "stopped", f"Stopped after {done} of {len(jobs)} jobs",
                       profile=profile_name, kind="build", data=counts)
                return
            progress(conn, run_id, done, f"Writing {done + 1} of {len(jobs)}: {job['title']} at {job['company']}")
            try:
                outcome = build_one(conn, settings, profile, job, runner, check_live, browser, settings.claude_model)
                counts[outcome] += 1
            except FatalBuildError:
                raise  # no point trying the next job
            except BuildError as exc:
                counts["failed"] += 1
                with transaction(conn):
                    log_event(conn, "build", f"Could not build {job['title']} at {job['company']}: {exc}",
                              profile=profile_name, level="warning")
        progress(conn, run_id, len(jobs))
        message = (f"Built {counts['built']} applications"
                   + (f", {counts['closed']} postings had closed" if counts["closed"] else "")
                   + (f", {counts['failed']} failed (see activity)" if counts["failed"] else ""))
        if not browser and counts["built"]:
            message += ". No Chrome or Edge found, so documents were saved as HTML only"
        finish(conn, run_id, "done", message, profile=profile_name, kind="build", data=counts)
    except Exception as exc:
        finish(conn, run_id, "failed", f"Build failed: {exc}", profile=profile_name, kind="build",
               level="error", data=counts)
    finally:
        conn.close()


def list_builds(conn: sqlite3.Connection, profile: str) -> list[dict]:
    rows = conn.execute(
        """SELECT b.*, j.title, j.company, j.location, j.url, j.status AS job_status, j.rank_score
           FROM builds b JOIN jobs j ON j.id = b.job_id
           WHERE b.profile = ? AND b.id = (SELECT MAX(id) FROM builds WHERE job_id = b.job_id)
           ORDER BY b.id DESC""",
        (profile,),
    ).fetchall()
    out = []
    for row in rows:
        item = dict(row)
        item["flags"] = json.loads(item["flags"] or "[]")
        out.append(item)
    return out
