"""The extension's LinkedIn scraper, run in a real (headless) Chromium on fake LinkedIn pages.

Two page styles:
- "classic": LinkedIn's usual markup (data-occludable-job-id, #job-details)
- "renamed": every class name changed, as happens when LinkedIn redesigns.
  The scraper must still find jobs through its fallbacks (job links,
  the "About the job" heading).

Skipped automatically when Playwright isn't installed (it is only a dev tool).
"""

from __future__ import annotations

import re

import pytest

from roleradar.settings import PROJECT_ROOT

playwright = pytest.importorskip("playwright.sync_api")

JOBS = [
    ("4011111111", "Junior AI Engineer (m/w/d)", "Acme GmbH", "Berlin, Germany (Hybrid)",
     "We build ML products. You have 1 year of experience with Python and PyTorch."),
    ("4022222222", "Machine Learning Engineer", "Beta AG", "Munich, Germany",
     "You bring 5+ years of experience in machine learning. Fluent German required."),
    ("4033333333", "Data Scientist", "Gamma SE", "Remote, Germany",
     "Analyse data with SQL and Python. English is our working language."),
]

CLASSIC = """
<ul class="jobs-search__results-list">{cards}</ul>
<div class="jobs-search__job-details--container"><div id="job-details"></div></div>
"""
CLASSIC_CARD = """
<li data-occludable-job-id="{id}">
  <div class="job-card-container" data-job-id="{id}">
    <a class="job-card-list__title--link" href="/jobs/view/{id}/" aria-label="{title}"><strong>{title}</strong></a>
    <div class="artdeco-entity-lockup__subtitle"><span>{company}</span></div>
    <div class="artdeco-entity-lockup__caption"><ul><li>{location}</li></ul></div>
  </div>
</li>"""

RENAMED = """
<div class="x9f2a"><ul>{cards}</ul></div>
<section class="q7d1"><div class="m3k9"><h2>About the job</h2><div class="r8s2"></div></div></section>
"""
RENAMED_CARD = """
<li class="z1b4"><div class="p0o9">
  <a class="h5k2" href="https://www.linkedin.com/jobs/view/{id}/?trk=x">{title}</a>
  <div class="u7e1">{company}</div><div class="u7e2">{location}</div>
</div></li>"""

# What LinkedIn does when you click a card: change the URL and fill the detail pane.
CLICK_SCRIPT = """
<script>
const descriptions = %s;
document.addEventListener('click', (e) => {
  const li = e.target.closest('li');
  if (!li) return;
  e.preventDefault();
  const id = li.dataset.occludableJobId || li.querySelector('a').href.match(/(\\d{10})/)[1];
  setTimeout(() => {
    history.pushState({}, '', '/jobs/search/?keywords=ai&currentJobId=' + id);
    const target = document.querySelector('#job-details') || document.querySelector('.r8s2');
    target.textContent = descriptions[id];
  }, 150);
}, true);
</script>
"""


def page_html(style: str) -> str:
    card, layout = (CLASSIC_CARD, CLASSIC) if style == "classic" else (RENAMED_CARD, RENAMED)
    cards = "".join(card.format(id=i, title=t, company=c, location=loc) for i, t, c, loc, _ in JOBS)
    descriptions = {i: d for i, *_, d in JOBS}
    import json
    return "<html><body>" + layout.format(cards=cards) + CLICK_SCRIPT % json.dumps(descriptions) + "</body></html>"


def scraper_source() -> str:
    source = (PROJECT_ROOT / "extension" / "linkedin.js").read_text()
    return re.sub(r"^export ", "", source, flags=re.MULTILINE)


@pytest.fixture(scope="module")
def browser():
    with playwright.sync_playwright() as p:
        try:
            chromium = p.chromium.launch()
        except Exception as exc:  # browsers not downloaded
            pytest.skip(f"Chromium not available: {exc}")
        yield chromium
        chromium.close()


def run_scraper(browser, html: str, url="https://www.linkedin.com/jobs/search/?keywords=ai") -> dict:
    page = browser.new_page()
    page.route("https://www.linkedin.com/**", lambda route: route.fulfill(body=html, content_type="text/html; charset=utf-8"))
    page.goto(url)
    result = page.evaluate(
        "async () => {" + scraper_source() + "; return await scrapeSearchPage({delayMin: 10, delayMax: 20, waitMs: 3000}); }"
    )
    page.close()
    return result


@pytest.mark.parametrize("style", ["classic", "renamed"])
def test_scraper_reads_every_job(browser, style):
    result = run_scraper(browser, page_html(style))
    assert result["problem"] is None
    jobs = {j["url"]: j for j in result["jobs"]}
    assert len(jobs) == 3
    for job_id, title, company, location, description in JOBS:
        job = jobs[f"https://www.linkedin.com/jobs/view/{job_id}/"]
        assert job["title"] == title
        assert job["company"] == company
        assert job["location"] == location
        assert job["description"] == description, style


def test_signed_out_is_reported(browser):
    result = run_scraper(browser, "<html><body>Sign in</body></html>", url="https://www.linkedin.com/authwall?x=1")
    assert result == {"jobs": [], "problem": "signed_out"}


def test_no_results_page(browser):
    result = run_scraper(browser, "<html><body><h1>No matching jobs found.</h1></body></html>")
    assert result == {"jobs": [], "problem": None}


def test_read_open_job(browser):
    html = """<html><body>
      <h1 class="job-details-jobs-unified-top-card__job-title">Junior AI Engineer</h1>
      <div class="job-details-jobs-unified-top-card__company-name">Acme GmbH</div>
      <div class="job-details-jobs-unified-top-card__primary-description-container">Berlin, Germany · 2 days ago</div>
      <div id="job-details">You have 1 year of Python.</div></body></html>"""
    page = browser.new_page()
    page.route("https://www.linkedin.com/**", lambda route: route.fulfill(body=html, content_type="text/html; charset=utf-8"))
    page.goto("https://www.linkedin.com/jobs/view/4011111111/")
    job = page.evaluate("() => {" + scraper_source() + "; return readOpenJob(); }")
    page.close()
    assert job == {"url": "https://www.linkedin.com/jobs/view/4011111111/", "title": "Junior AI Engineer",
                   "company": "Acme GmbH", "location": "Berlin, Germany",
                   "description": "You have 1 year of Python."}
