import pytest

from pipeline.filters import JobFilter, apply_filters


def job(title, location="London"):
    return {"title": title, "location": location}


@pytest.fixture(scope="module")
def tier_a(filter_config):
    return JobFilter(filter_config, "a")


@pytest.fixture(scope="module")
def tier_b(filter_config):
    return JobFilter(filter_config, "b")


# --- rules shared by both tiers ---

@pytest.mark.parametrize("title", [
    "AI Engineer",
    "Machine Learning Engineer",
    "ML Engineers",                     # plural
    "Applied Scientist",
    "Senior Platform Engineer",         # senior is allowed
    "LLM Developer",
    "AI/ML Engineer",                   # slash is a word boundary
    "Engineer, Security (Remote)",
    "site-reliability engineer",        # hyphen for space, lowercase
])
def test_kept_by_both_tiers(tier_a, tier_b, title):
    assert tier_a.keep(job(title))
    assert tier_b.keep(job(title))


@pytest.mark.parametrize("title", [
    "AI Product Lead",                  # no engineer/developer/scientist
    "Machine Learning Engineering Lead",  # "engineering" is not "engineer"
    "Staff AI Engineer",
    "Principal ML Scientist",
    "Director of AI Engineering",
    "Head of Platform Engineering",
    "VP, Engineering",
    "Vice President Software Engineer",
    "ML Engineer Manager",
    "Tech Lead, AI Engineer",
    "Lead Machine Learning Engineer",
    "ML Engineer (Team Lead)",
    "Tech-Lead ML Engineer",
    "Frontend Engineer, AI",
    "Front-End AI Engineer",
    "Front End Developer (AI Platform)",
    "React Native Engineer - AI",
    "iOS Engineer, AI",
    "Android Engineers - ML",
    "Mobile Platform Engineer",
    "AI Designer / Developer",
    "Sales Engineer",
    "Pre-Sales Engineer",
    "Pre Sales Engineer, AI",
    "Presales Solutions Engineer",
    "GTM Engineer",
    "Martech Engineer",
    "Website Growth Engineer",
    "Marketing Data Scientist",
    "Automation Engineer - Influencers",
])
def test_dropped_by_both_tiers(tier_a, tier_b, title):
    assert not tier_a.keep(job(title))
    assert not tier_b.keep(job(title))


@pytest.mark.parametrize("title", [
    "Staffing Systems Engineer (AI)",   # "staffing" is not "staff"
    "Platform Engineer, Headcount Tools",  # "head" alone is not "head of"
    "AI Engineer, Management Tools",    # "management" is not "manager"
    "ML Engineer, Audios",              # "ios" inside a word
    "AI Engineer, Leadership Tools",    # "leadership" is not "lead"
    "AI Solutions Engineer",            # solutions engineers are kept
    "Salesforce Platform Engineer",     # "salesforce" is not "sales"
])
def test_exclusions_use_word_boundaries(tier_a, tier_b, title):
    assert tier_a.keep(job(title))
    assert tier_b.keep(job(title))


# --- role terms: required for tier B only ---

@pytest.mark.parametrize("title", [
    "Backend Engineer",
    "Solutions Engineer",               # kept in tier A, no role term for tier B
    "Software Engineers (Ruby)",
    "Data Scientist",
    "Engineer, Maintainability",        # "ai" inside "maintain"
    "HTML Developer",                   # "ml" inside "html"
    "Email Deliverability Engineer",    # "ai" inside "email"
    "Cloudflare Developer",             # "cloud" inside a word
    "Unapplied Research Scientist",     # "applied" inside a word
])
def test_no_role_term_kept_by_a_dropped_by_b(tier_a, tier_b, title):
    assert tier_a.keep(job(title))
    assert not tier_b.keep(job(title))


@pytest.mark.parametrize("title", [
    "Gen AI Engineer",
    "GenAI Engineer",
    "Generative AI Developer",
    "MLOps Engineer",
    "LLMOps Engineer",
    "DevSecOps Engineer",
    "Site Reliability Engineer",
    "SRE - Software Engineer",
    "Software Engineer (Infrastructure)",
    "Cloud Security Engineer",
    "Software Engineer - A.I. & ML",
])
def test_role_terms_match_for_tier_b(tier_b, title):
    assert tier_b.keep(job(title))


# --- location ---

@pytest.mark.parametrize("location", [
    "",
    "   ",
    "London",
    "UK - London",
    "Cardiff, London or Remote (UK)",
    "United Kingdom (Hybrid)",
    "Manchester, England",
    "Remote",
    "Remote - Europe",
    "EMEA",
    "Remote (EMEA)",
    "Europe",
    "London or New York",               # a UK term always passes
    "Remote - UK or Ireland",
])
def test_location_kept(tier_a, location):
    assert tier_a.keep(job("AI Engineer", location))


@pytest.mark.parametrize("location", [
    "New York",
    "Berlin, Germany",
    "Ukraine",              # "uk" inside a word
    "Londonderry, NH",      # "london" inside a word
    "Milwaukee",
    "São Paulo, BR",
    "Remote - USA",
    "Remote, Canada",
    "Remote (US)",
    "Remote - U.S.",
    "Remote, United States",
    "Remote - San Francisco",
    "New York (Remote)",
    "Remote - Europe (Poland)",
    "EMEA - Germany",
    "Remote, North America",
])
def test_location_dropped(tier_a, location):
    assert not tier_a.keep(job("AI Engineer", location))


# --- config drives the behaviour ---

def test_tier_rule_is_configurable(filter_config):
    strict = dict(filter_config, tiers={"a": {"require_role_term": True}})
    jobs = [job("Backend Engineer"), job("AI Engineer")]
    assert [j["title"] for j in apply_filters(jobs, strict, "a")] == ["AI Engineer"]
    assert len(apply_filters(jobs, filter_config, "a")) == 2


def test_term_lists_are_configurable(filter_config):
    locations = {"uk": ["leeds"], "broad": ["remote"], "exclude": ["mars"]}
    custom = dict(filter_config, title_exclude_seniority=["senior"], locations=locations)
    jobs = [
        job("Senior AI Engineer", "Leeds"),
        job("AI Engineer", "Leeds"),
        job("AI Engineer"),                     # London is no longer listed
        job("AI Engineer", "Remote - USA"),     # USA is no longer excluded
        job("AI Engineer", "Remote - Mars"),
    ]
    assert apply_filters(jobs, custom, "a") == [job("AI Engineer", "Leeds"), job("AI Engineer", "Remote - USA")]


def test_empty_exclude_list_excludes_nothing(filter_config):
    locations = dict(filter_config["locations"], exclude=[])
    custom = dict(filter_config, locations=locations)
    assert apply_filters([job("AI Engineer", "Remote - USA")], custom, "a")


# --- every Tier B search lane has a role term that lets its titles through ---

LANES = {
    "cloud": "Cloud Engineer",
    "sre": "SRE Engineer",
    "site reliability": "Site Reliability Engineer",
    "devops": "DevOps Engineer",
    "devsecops": "DevSecOps Engineer",
    "security": "Security Engineer",
    "platform": "Platform Engineer",
    "applied": "Applied Scientist",
    "generative": "Generative Models Engineer",
    "mlops": "MLOps Engineer",
}


@pytest.mark.parametrize("term, title", sorted(LANES.items()))
def test_tier_b_lane_is_covered(filter_config, tier_b, term, title):
    assert term in filter_config["role_terms"]
    assert tier_b.keep(job(title))
    # the title passes because of this term, not another one
    only = dict(filter_config, role_terms=[term])
    assert JobFilter(only, "b").keep(job(title))
    without = dict(filter_config, role_terms=[t for t in filter_config["role_terms"] if t != term])
    assert not JobFilter(without, "b").keep(job(title))
