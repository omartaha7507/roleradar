"""Keys that decide when two job ads are "the same job".

url_key   The same posting seen twice (same LinkedIn job id, or the same URL
          after removing tracking junk). Stops duplicates on re-scrapes.

role_key  The same role re-posted under a new URL: same company + same title,
          ignoring legal suffixes (GmbH, AG ...) and German gender markers
          like "(m/w/d)". Stops you applying twice to a re-posted job.
"""

from __future__ import annotations

import re
import unicodedata
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

_LINKEDIN_ID = [
    re.compile(r"linkedin\.com/jobs/view/(?:[^/?#]*-)?(\d{6,})"),
    re.compile(r"[?&](?:currentJobId|jobId)=(\d{6,})"),
]

_TRACKING_PARAMS = {"refid", "trackingid", "trk", "ref", "src", "source", "position", "pagenum", "eboid"}

# "(m/w/d)", "(f/m/x)", "(all genders)", "m/f/d" ... common in German job titles.
_GENDER_MARKER = re.compile(
    r"\(?\s*\b(?:[mwfdx]\s*/\s*){2,3}[mwfdx]\b\s*\)?|\(\s*all\s+genders?\s*\)|\(\s*gn\s*\)",
    re.IGNORECASE,
)
_COMPANY_SUFFIX = re.compile(
    r"\b(?:gmbh(?:\s*&\s*co\.?\s*kg)?|ag|se|kg|ug|e\.?\s*v|inc|llc|ltd|limited|plc|corp(?:oration)?|co|b\.?\s*v|s\.?\s*a)\.?$",
    re.IGNORECASE,
)


def site_of(url: str) -> str:
    host = urlsplit(url).netloc.lower()
    for site in ("linkedin", "indeed", "stepstone", "arbeitnow", "wuzzuf", "bayt"):
        if site in host:
            return site
    return "other"


def url_key(url: str) -> str:
    url = url.strip()
    for pattern in _LINKEDIN_ID:
        match = pattern.search(url)
        if match:
            return f"linkedin:{match.group(1)}"

    parts = urlsplit(url)
    query = [
        (k, v)
        for k, v in parse_qsl(parts.query, keep_blank_values=False)
        if k.lower() not in _TRACKING_PARAMS and not k.lower().startswith("utm_")
    ]
    return urlunsplit(
        (
            parts.scheme.lower() or "https",
            parts.netloc.lower().removeprefix("www."),
            parts.path.rstrip("/") or "/",
            urlencode(sorted(query)),
            "",  # the #fragment never identifies a different job
        )
    )


def _plain(text: str) -> str:
    # "Müller & Söhne" -> "muller & sohne": accents removed, lowercase.
    text = unicodedata.normalize("NFKD", text)
    return "".join(c for c in text if not unicodedata.combining(c)).lower()


def normalize_company(company: str) -> str:
    text = _plain(company).strip()
    # Strip suffixes repeatedly: "Foo GmbH & Co. KG" -> "foo"
    while True:
        stripped = _COMPANY_SUFFIX.sub("", text).strip(" ,.-")
        if stripped == text:
            break
        text = stripped
    return re.sub(r"[^a-z0-9]+", " ", text).strip()


def normalize_title(title: str) -> str:
    text = _GENDER_MARKER.sub(" ", _plain(title))
    return re.sub(r"[^a-z0-9+#]+", " ", text).strip()


def role_key(company: str, title: str) -> str:
    return f"{normalize_company(company)}|{normalize_title(title)}"
