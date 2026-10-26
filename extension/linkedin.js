// Functions that run INSIDE a LinkedIn tab (injected with chrome.scripting.executeScript).
//
// Chrome copies only the function's own source code into the page, so each
// function must be self-contained: no imports, no outside variables.
//
// LinkedIn changes its page structure often. So every lookup tries known
// selectors first and falls back to something sturdier: links to
// "/jobs/view/<id>" for the job list, and the "About the job" heading for
// the description. If a field still can't be found, the job is sent
// without it rather than dropped.

export async function scrapeSearchPage(options) {
  const { delayMin = 1200, delayMax = 2400, waitMs = 15000 } = options || {};
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const pause = () => sleep(delayMin + Math.random() * (delayMax - delayMin));
  const text = (el) => (el ? el.innerText || el.textContent || "" : "").replace(/\s+\n/g, "\n").trim();
  const firstLine = (s) => (s || "").split("\n").map((x) => x.trim()).find(Boolean) || "";

  if (/\/(login|authwall|checkpoint)/.test(location.pathname)) {
    return { jobs: [], problem: "signed_out" };
  }

  // 1. Wait for the job list (or a "no results" message).
  const jobIdOf = (el) => {
    const own = el.getAttribute("data-occludable-job-id") || el.getAttribute("data-job-id");
    if (own) return own;
    const link = el.querySelector('a[href*="/jobs/view/"], a[href*="currentJobId="]');
    const m = link && link.href.match(/(?:\/jobs\/view\/(?:[^/?#]*-)?|currentJobId=)(\d{6,})/);
    return m ? m[1] : null;
  };
  const findCards = () => {
    let cards = [...document.querySelectorAll("li[data-occludable-job-id], div[data-job-id]")];
    if (!cards.length) {
      // Fallback: every list item that links to a job.
      cards = [...document.querySelectorAll('a[href*="/jobs/view/"]')]
        .map((a) => a.closest("li") || a.parentElement)
        .filter(Boolean);
    }
    const seen = new Set();
    return cards.filter((c) => {
      const id = jobIdOf(c);
      if (!id || seen.has(id)) return false;
      seen.add(id);
      return true;
    });
  };
  const noResults = () =>
    document.querySelector(".jobs-search-no-results-banner, .jobs-search-two-pane__no-results-banner--expand") ||
    /No matching jobs found|Keine passenden Jobs/i.test(document.body.innerText.slice(0, 5000));

  const started = Date.now();
  while (!findCards().length && !noResults() && Date.now() - started < waitMs) await sleep(500);
  if (noResults()) return { jobs: [], problem: null };
  if (!findCards().length) return { jobs: [], problem: "no_cards" };

  // 2. LinkedIn loads cards as you scroll: scroll the list to the bottom in steps.
  const firstCard = findCards()[0];
  let scroller = firstCard.parentElement;
  while (scroller && scroller !== document.body && scroller.scrollHeight <= scroller.clientHeight + 10) {
    scroller = scroller.parentElement;
  }
  scroller = scroller && scroller !== document.body ? scroller : document.scrollingElement;
  for (let i = 0; i < 12; i++) {
    scroller.scrollBy(0, 600);
    await sleep(350);
  }
  scroller.scrollTo(0, 0);

  // 3. Read each card, open it, and read the description from the detail pane.
  const descriptionNow = () => {
    const known = document.querySelector(
      "#job-details, .jobs-description__content, .jobs-box__html-content, .jobs-description-content__text"
    );
    if (known && text(known).length > 50) return text(known);
    const heading = [...document.querySelectorAll("h2, h3")].find((h) => /About the job|Info zum Job|Über den Job/i.test(text(h)));
    return heading ? text(heading.parentElement).replace(/^(About the job|Info zum Job|Über den Job)\s*/i, "") : "";
  };

  const jobs = [];
  for (const card of findCards()) {
    const id = jobIdOf(card);
    const titleEl = card.querySelector(
      ".job-card-list__title--link, a.job-card-container__link, .job-card-list__title, a[href*='/jobs/view/']"
    );
    let title = firstLine(titleEl && (titleEl.getAttribute("aria-label") || text(titleEl)));
    title = title.replace(/\s+with verification$/i, "");
    const lines = text(card).split("\n").map((x) => x.trim()).filter(Boolean)
      .filter((line, i, all) => all.indexOf(line) === i);
    const after = lines.slice(Math.max(0, lines.indexOf(title)) + 1);
    const company =
      firstLine(text(card.querySelector(".artdeco-entity-lockup__subtitle, .job-card-container__primary-description, .job-card-container__company-name"))) ||
      after[0] || "";
    const place =
      firstLine(text(card.querySelector(".artdeco-entity-lockup__caption, .job-card-container__metadata-item, .job-card-container__metadata-wrapper li"))) ||
      after[1] || "";
    if (!title) continue;

    const before = descriptionNow();
    (card.querySelector("div[data-job-id], .job-card-container") || card).scrollIntoView({ block: "center" });
    (card.querySelector("div[data-job-id], .job-card-container, .job-card-list") || titleEl || card).click();
    // Wait until the detail pane shows THIS job: the URL names it and the text changed.
    const opened = Date.now();
    let description = "";
    while (Date.now() - opened < 8000) {
      await sleep(400);
      description = descriptionNow();
      if (location.href.includes(id) && description && description !== before) break;
    }
    jobs.push({
      url: `https://www.linkedin.com/jobs/view/${id}/`,
      title: title.slice(0, 300),
      company: (company || "Unknown company").slice(0, 200),
      location: place ? place.slice(0, 200) : null,
      description: description && description !== before ? description.slice(0, 100000) : null,
    });
    await pause();
  }
  return { jobs, problem: null };
}

// Reads the job currently open on a LinkedIn page (a /jobs/view/ page, or the
// detail pane of a search). Used by the popup's "Add this job" and "Mark applied".
export function readOpenJob() {
  const text = (el) => (el ? el.innerText || el.textContent || "" : "").trim();
  const firstLine = (s) => (s || "").split("\n").map((x) => x.trim()).find(Boolean) || "";
  const m = location.href.match(/(?:\/jobs\/view\/(?:[^/?#]*-)?|currentJobId=)(\d{6,})/);
  if (!m) return null;
  const title = firstLine(text(document.querySelector(
    ".job-details-jobs-unified-top-card__job-title, .jobs-unified-top-card__job-title, h1"
  )));
  const company = firstLine(text(document.querySelector(
    ".job-details-jobs-unified-top-card__company-name, .jobs-unified-top-card__company-name"
  )));
  const place = firstLine(text(document.querySelector(
    ".job-details-jobs-unified-top-card__primary-description-container, .job-details-jobs-unified-top-card__tertiary-description-container, .jobs-unified-top-card__bullet"
  ))).split("·")[0].trim();
  let description = text(document.querySelector("#job-details, .jobs-description__content, .jobs-box__html-content"));
  if (!description) {
    const heading = [...document.querySelectorAll("h2, h3")].find((h) => /About the job|Info zum Job|Über den Job/i.test(text(h)));
    description = heading ? text(heading.parentElement) : "";
  }
  return {
    url: `https://www.linkedin.com/jobs/view/${m[1]}/`,
    title: title || null,
    company: company || null,
    location: place || null,
    description: description || null,
  };
}
