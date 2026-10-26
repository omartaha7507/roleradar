"""The CV and cover letter: their shape, the honesty checks, and the HTML layout.

Claude returns the content as JSON in exactly this shape. RoleRadar, not
Claude, decides how it looks: the layout below is fixed, so every CV is
clean, one column, and readable by applicant-tracking systems (ATS).

Before anything is saved, `check_facts` compares the content against the
person's own profile files. A company, school or project that isn't in the
profile means Claude invented something, and the document is rejected.
"""

from __future__ import annotations

import html
import re
from datetime import date

from pydantic import BaseModel, Field

# --- Shape ------------------------------------------------------------------------------


class Contact(BaseModel):
    name: str
    email: str
    phone: str | None = None
    city: str | None = None
    linkedin: str | None = None
    github: str | None = None


class SkillGroup(BaseModel):
    label: str = Field(max_length=40)
    items: list[str] = Field(min_length=1, max_length=14)


class Experience(BaseModel):
    role: str
    company: str
    location: str | None = None
    dates: str
    bullets: list[str] = Field(min_length=1, max_length=5)


class Project(BaseModel):
    name: str
    link: str | None = None
    stack: str | None = None
    bullets: list[str] = Field(min_length=1, max_length=3)


class Education(BaseModel):
    degree: str
    school: str
    dates: str
    details: list[str] = Field(default_factory=list, max_length=3)


class CV(BaseModel):
    contact: Contact
    headline: str = Field(max_length=90)
    summary: str = Field(max_length=600)
    skills: list[SkillGroup] = Field(min_length=1, max_length=6)
    experience: list[Experience] = Field(default_factory=list, max_length=6)
    projects: list[Project] = Field(default_factory=list, max_length=5)
    education: list[Education] = Field(min_length=1, max_length=4)
    languages: list[str] = Field(default_factory=list, max_length=6)


class Letter(BaseModel):
    greeting: str = Field(max_length=120)
    paragraphs: list[str] = Field(min_length=2, max_length=5)
    closing: str = Field(max_length=60)


class Fit(BaseModel):
    summary: str = Field(max_length=400, description="One or two sentences: why this person fits this job")
    flags: list[str] = Field(default_factory=list, max_length=5,
                             description="Gaps or requirements worth knowing before applying")


class Application(BaseModel):
    language: str = Field(pattern="^(en|de)$")
    cv: CV
    letter: Letter
    fit: Fit


# --- Honesty checks -----------------------------------------------------------------------------

_EM_DASH = re.compile(r"\s*—\s*")           # "Engineer — Python" -> "Engineer, Python"
_EN_DASH_SPACED = re.compile(r"\s+–\s+")     # "2021 – 2025" -> "2021 - 2025"


def clean_dashes(value):
    """Em and en dashes read as machine-written; replace them everywhere."""
    if isinstance(value, str):
        value = _EM_DASH.sub(", ", value)
        value = _EN_DASH_SPACED.sub(" - ", value).replace("–", "-")
        return value.replace(",,", ",").strip().rstrip(",")
    if isinstance(value, list):
        return [clean_dashes(v) for v in value]
    if isinstance(value, dict):
        return {k: clean_dashes(v) for k, v in value.items()}
    return value


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def _mentioned(name: str, source: str) -> bool:
    """Is `name` (a company, school, project) written in the profile? Ignores case and punctuation."""
    name = _norm(name)
    return bool(name) and name in _norm(source)


def check_facts(app: Application, identity_md: str, projects_md: str) -> list[str]:
    """Everything that could not be found in the profile. Empty list = all good."""
    problems = []
    contact = app.cv.contact
    if not _mentioned(contact.name, identity_md):
        problems.append(f"Name '{contact.name}' is not in identity.md")
    if contact.email.lower() not in identity_md.lower():
        problems.append(f"Email '{contact.email}' is not in identity.md")
    for job in app.cv.experience:
        if not _mentioned(job.company, identity_md):
            problems.append(f"Employer '{job.company}' is not in identity.md")
    for school in app.cv.education:
        if not _mentioned(school.school, identity_md):
            problems.append(f"School '{school.school}' is not in identity.md")
    for project in app.cv.projects:
        if not _mentioned(project.name, projects_md):
            problems.append(f"Project '{project.name}' is not in projects.md")
    return problems


# --- Layout -------------------------------------------------------------------------------------------

CSS = """
@page { size: A4; margin: 14mm 15mm; }
* { box-sizing: border-box; }
body { font-family: "Segoe UI", "Helvetica Neue", Arial, sans-serif; font-size: 10pt; line-height: 1.38;
       color: #1f1e1c; margin: 0; }
h1 { font-size: 20pt; font-weight: 600; margin: 0; letter-spacing: -0.01em; }
.headline { font-size: 11pt; color: #0f6e56; margin: 2px 0 6px; }
.contact { color: #5f5e5a; font-size: 9pt; }
.contact span + span::before { content: "  |  "; color: #b4b2a9; }
h2 { font-size: 9.5pt; text-transform: uppercase; letter-spacing: 0.06em; color: #0f6e56; margin: 14px 0 6px;
     padding-bottom: 3px; border-bottom: 1px solid #d3d1c7; }
.item { margin: 0 0 8px; break-inside: avoid; }
.row { display: flex; justify-content: space-between; gap: 12px; }
.row b { font-weight: 600; }
.muted { color: #5f5e5a; }
ul { margin: 3px 0 0; padding-left: 16px; }
li { margin: 1px 0; }
.skills div { margin: 1px 0; }
.letter p { margin: 0 0 10px; }
.letter .meta { margin: 18px 0 18px; color: #5f5e5a; }
"""


def _e(text: str | None) -> str:
    return html.escape(text or "")


def _contact_line(c: Contact) -> str:
    parts = [c.city, c.phone, c.email, c.linkedin, c.github]
    return "".join(f"<span>{_e(p)}</span>" for p in parts if p)


def _page(title: str, body: str, lang: str) -> str:
    return (f'<!doctype html><html lang="{lang}"><head><meta charset="utf-8"><title>{_e(title)}</title>'
            f"<style>{CSS}</style></head><body>{body}</body></html>")


def render_cv(app: Application) -> str:
    cv = app.cv
    de = app.language == "de"
    head = (f"<h1>{_e(cv.contact.name)}</h1><div class='headline'>{_e(cv.headline)}</div>"
            f"<div class='contact'>{_contact_line(cv.contact)}</div>")
    parts = [head, f"<h2>{'Profil' if de else 'Profile'}</h2><p>{_e(cv.summary)}</p>"]

    skills = "".join(f"<div><b>{_e(g.label)}:</b> {_e(', '.join(g.items))}</div>" for g in cv.skills)
    parts.append(f"<h2>{'Kenntnisse' if de else 'Skills'}</h2><div class='skills'>{skills}</div>")

    if cv.experience:
        items = "".join(
            f"<div class='item'><div class='row'><span><b>{_e(x.role)}</b>, {_e(x.company)}"
            f"{', ' + _e(x.location) if x.location else ''}</span><span class='muted'>{_e(x.dates)}</span></div>"
            f"<ul>{''.join(f'<li>{_e(b)}</li>' for b in x.bullets)}</ul></div>"
            for x in cv.experience)
        parts.append(f"<h2>{'Berufserfahrung' if de else 'Experience'}</h2>{items}")

    if cv.projects:
        items = "".join(
            f"<div class='item'><div class='row'><span><b>{_e(p.name)}</b>"
            f"{' <span class=muted>(' + _e(p.stack) + ')</span>' if p.stack else ''}</span>"
            f"<span class='muted'>{_e(p.link)}</span></div>"
            f"<ul>{''.join(f'<li>{_e(b)}</li>' for b in p.bullets)}</ul></div>"
            for p in cv.projects)
        parts.append(f"<h2>{'Projekte' if de else 'Projects'}</h2>{items}")

    items = "".join(
        f"<div class='item'><div class='row'><span><b>{_e(s.degree)}</b>, {_e(s.school)}</span>"
        f"<span class='muted'>{_e(s.dates)}</span></div>"
        + (f"<ul>{''.join(f'<li>{_e(d)}</li>' for d in s.details)}</ul>" if s.details else "") + "</div>"
        for s in cv.education)
    parts.append(f"<h2>{'Ausbildung' if de else 'Education'}</h2>{items}")

    if cv.languages:
        parts.append(f"<h2>{'Sprachen' if de else 'Languages'}</h2><p>{_e(', '.join(cv.languages))}</p>")
    return _page(f"CV {cv.contact.name}", "".join(parts), app.language)


def render_letter(app: Application, company: str, job_title: str, today: date | None = None) -> str:
    c = app.cv.contact
    today = today or date.today()
    when = today.strftime("%d.%m.%Y") if app.language == "de" else today.strftime("%d %B %Y")
    subject = f"{'Bewerbung als' if app.language == 'de' else 'Application for'} {job_title}"
    body = (
        f"<div class='letter'><h1>{_e(c.name)}</h1><div class='contact'>{_contact_line(c)}</div>"
        f"<div class='meta'>{_e(company)}<br>{_e(when)}</div>"
        f"<p><b>{_e(subject)}</b></p><p>{_e(app.letter.greeting)}</p>"
        + "".join(f"<p>{_e(p)}</p>" for p in app.letter.paragraphs)
        + f"<p>{_e(app.letter.closing)}<br>{_e(c.name)}</p></div>"
    )
    return _page(f"Cover letter {c.name}", body, app.language)
