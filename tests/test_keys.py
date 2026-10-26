from roleradar.keys import normalize_company, normalize_title, role_key, site_of, url_key


def test_linkedin_urls_with_different_tracking_share_a_key():
    a = "https://www.linkedin.com/jobs/view/4012345678/?refId=abc&trackingId=xyz"
    b = "https://de.linkedin.com/jobs/view/junior-ai-engineer-at-acme-4012345678"
    c = "https://www.linkedin.com/jobs/search/?currentJobId=4012345678&keywords=ai"
    assert url_key(a) == url_key(b) == url_key(c) == "linkedin:4012345678"


def test_other_urls_drop_tracking_and_fragment():
    a = "https://WWW.Example.com/careers/42/?utm_source=x&id=7#apply"
    b = "https://example.com/careers/42?id=7"
    assert url_key(a) == url_key(b)


def test_different_query_ids_stay_different():
    assert url_key("https://example.com/job?id=1") != url_key("https://example.com/job?id=2")


def test_company_suffixes_removed():
    assert normalize_company("Acme GmbH") == "acme"
    assert normalize_company("ACME GmbH & Co. KG") == "acme"
    assert normalize_company("Müller Software AG") == "muller software"


def test_gender_markers_removed_from_titles():
    assert normalize_title("Junior AI Engineer (m/w/d)") == "junior ai engineer"
    assert normalize_title("Junior AI Engineer (f/m/x)") == "junior ai engineer"
    assert normalize_title("Junior AI Engineer (all genders)") == "junior ai engineer"
    assert normalize_title("C++ Developer m/f/d") == "c++ developer"


def test_role_key_matches_reposts():
    assert role_key("Acme GmbH", "Junior AI Engineer (m/w/d)") == role_key("ACME", "Junior AI Engineer")


def test_site_of():
    assert site_of("https://de.linkedin.com/jobs/view/1") == "linkedin"
    assert site_of("https://www.stepstone.de/x") == "stepstone"
    assert site_of("https://jobs.example.org") == "other"
