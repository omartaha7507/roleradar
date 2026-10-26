"""Is the job still open? Checked right before writing a CV for it.

Writing a CV takes Claude a minute or two. Spending that on a posting that
closed last week is waste, and job ads close fast. So just before building,
RoleRadar loads the public job page and looks for "no longer accepting
applications" (and the German versions).

Three answers:
- "open":    the page loads and shows no closed notice
- "closed":  the page is gone (404/410) or says it is closed
- "unknown": the site refused to answer (LinkedIn sometimes blocks
             logged-out visits). Unknown jobs are kept: better one wasted
             CV than a missed job.
"""

from __future__ import annotations

import re
import urllib.error
import urllib.request
from typing import Literal

Status = Literal["open", "closed", "unknown"]

CLOSED_MARKERS = re.compile(
    r"no longer accepting applications|this job is no longer available|job has expired|"
    r"position has been filled|posting has closed|"
    r"nimmt keine bewerbungen mehr an|bewerbungen werden nicht mehr angenommen|"
    r"stelle ist nicht mehr verfügbar|stellenanzeige ist nicht mehr verfügbar|nicht mehr aktiv",
    re.IGNORECASE,
)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/130.0 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9,de;q=0.8",
}


def check(url: str, timeout: float = 15) -> tuple[Status, str]:
    """Returns (status, short explanation)."""
    request = urllib.request.Request(url, headers=HEADERS)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            final_url = response.geturl()
            html = response.read(600_000).decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        if exc.code in (404, 410):
            return "closed", f"The posting is gone (HTTP {exc.code})"
        return "unknown", f"The site answered HTTP {exc.code}"
    except Exception as exc:  # network trouble, timeouts, odd TLS
        return "unknown", f"Could not reach the site: {type(exc).__name__}"

    if CLOSED_MARKERS.search(html):
        return "closed", "The posting says it no longer accepts applications"
    # LinkedIn sends expired job pages on to a search page.
    if "linkedin.com/jobs/view/" in url and "/jobs/view/" not in final_url and "/jobs/search" in final_url:
        return "closed", "LinkedIn redirected the expired posting to a search"
    if "linkedin.com" in final_url and ("authwall" in final_url or "/login" in final_url):
        return "unknown", "LinkedIn asked for a login"
    return "open", "The posting is online"
