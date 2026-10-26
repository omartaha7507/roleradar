"""Advice on saved search URLs.

The cheapest filter is the one LinkedIn applies before you ever download a
job. A search without a "posted within" or "experience level" filter floods
the pipeline with old or senior ads that every later step then has to reject.
So when a search is saved, RoleRadar checks the URL and suggests a better one.
"""

from __future__ import annotations

from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

ONE_WEEK = 604_800  # seconds, as LinkedIn writes it: f_TPR=r604800

LINKEDIN_EXPERIENCE = {"1": "internship", "2": "entry level", "3": "associate",
                       "4": "mid-senior", "5": "director", "6": "executive"}


def linkedin_advice(url: str) -> tuple[list[str], str | None]:
    """Return (hints, suggested_url). suggested_url is None when nothing to change."""
    parts = urlsplit(url)
    if "linkedin.com" not in parts.netloc.lower() or "/jobs" not in parts.path:
        return [], None

    params = dict(parse_qsl(parts.query))
    hints: list[str] = []

    tpr = params.get("f_TPR", "")
    seconds = int(tpr[1:]) if tpr.startswith("r") and tpr[1:].isdigit() else None
    if seconds is None:
        hints.append("No 'date posted' filter: older ads are often closed by the time you apply. "
                     "Suggested: past week (f_TPR=r604800).")
        params["f_TPR"] = f"r{ONE_WEEK}"
    elif seconds > ONE_WEEK:
        hints.append(f"'Date posted' covers {seconds // 86_400} days. Past week keeps the pool fresh.")
        params["f_TPR"] = f"r{ONE_WEEK}"

    if "f_E" not in params:
        hints.append("No experience filter: senior roles will flood the pool. "
                     "Suggested: entry level + associate (f_E=2,3).")
        params["f_E"] = "2,3"
    else:
        senior = [LINKEDIN_EXPERIENCE.get(code, code) for code in params["f_E"].split(",")
                  if code in {"4", "5", "6"}]
        if senior:
            hints.append(f"Experience filter includes {', '.join(senior)} roles.")

    if params.get("location") and "geoId" not in params:
        hints.append("Location is typed but not pinned with geoId, so LinkedIn may widen the area. "
                     "Pick the place from LinkedIn's dropdown and copy the URL again.")

    if not hints:
        return [], None
    suggested = urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(params, safe=","), ""))
    changed = suggested != url
    return hints, suggested if changed else None
