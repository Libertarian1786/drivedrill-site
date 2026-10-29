#!/usr/bin/env python3
"""
Builds the generated pages of drivedrill.app from DriveDrill's own question packs:

  practice/<state-slug>/index.html   English: a 25-question sample quiz per state (51), a guide to
                                     the state's test, a FAQ, links to nearby states, and
                                     FAQPage + BreadcrumbList JSON-LD
  practice/index.html                English: the list of all 51
  es/<state-slug>/index.html         Spanish twin of every state page (51), from the
                                     Spanish packs; templates in tools/build_es_pages.py
  es/index.html                      ONLY the state list between its BEGIN/END markers
  sitemap.xml                        every page on the site (hand-written ones in STATIC_PAGES
                                     and LEGAL_PAGES below; hreflang pairs in SITEMAP_HREFLANG)

then points every App Store button at its address in tools/store_links.py and runs
tools/check_site.py (parse, links, head tags, hreflang pairs, store links).

The packs are read, never written, from the DriveDrill app repo AT A GIT COMMIT, so a build
is reproducible and does not depend on which branch that repo has checked out. APP_REF below
is the commit of the version on the App Store; bump it when a version with new questions goes
live. --app-ref builds from another commit; --content reads an already extracted Content dir.
An English state page and its Spanish twin always show the same 25 questions. The build also
writes the app's MobileApplication JSON-LD into the hand-written pages in APP_JSONLD_PAGES.

Usage:
    python tools/build_state_pages.py [--app-ref REF | --content DIR]
"""
from __future__ import annotations

import argparse
import html
import io
import json
import random
import re
import subprocess
import sys
import tarfile
import tempfile
from datetime import date
from pathlib import Path

TOOLS = Path(__file__).resolve().parent
SITE_ROOT = TOOLS.parent
sys.path.insert(0, str(TOOLS))

import store_links  # noqa: E402  (tools/store_links.py: the one place store URLs live)

APP_REPO = Path(r"C:\dev\DriveDrill-DMV")
# Version 1.8.14 (build 61), live on the App Store since 2026-10-26. Its car question packs
# match 1.6.1 (1d31d68, the ref before 2026-10-29) question for question; only each pack's
# "updated" date moved.
APP_REF = "ada6d0d1"
CONTENT_IN_REPO = "DriveDrill/Resources/Content"

APP_STORE_URL = store_links.STORE_LINKS["car"]
SITE_URL = "https://drivedrill.app"
TODAY = date.today().isoformat()

NUM_QUIZ_QUESTIONS = 25
NUM_STATE_SPECIFIC = 10  # of the 25, how many must be state-specific (id "<code>-...")

# Search results show about this much of a title; every generated title stays within it.
TITLE_MAX = 60

# The app's topic labels (DriveDrill/Models/Question.swift, QuestionTag.labels and .order, at
# APP_REF). The Spanish ones are the app's own, from Localizable.xcstrings (build_es_pages.py).
TOPIC_ORDER = ["signs", "signals", "right-of-way", "speed", "passing", "parking",
               "sharing-road", "impairment", "emergencies", "conditions", "vehicle", "licensing"]
TOPIC_EN = {
    "signs": "Road Signs", "signals": "Signals & Markings", "right-of-way": "Right of Way",
    "speed": "Speed & Following", "passing": "Passing & Lane Use", "parking": "Parking & Stopping",
    "sharing-road": "Sharing the Road", "impairment": "Alcohol & Drugs",
    "emergencies": "Emergencies & Crashes", "conditions": "Weather & Night",
    "vehicle": "Vehicle & Equipment", "licensing": "Licensing & Penalties",
}

# Which states share a land border (DC: Maryland and Virginia). Four Corners point contacts are
# not borders. Alaska and Hawaii have none, so their pages link a few other states instead
# (OTHER_STATES), under a different heading. The build checks the map is symmetric.
NEIGHBORS = {
    "AL": "FL GA MS TN", "AK": "", "AZ": "CA NV UT NM", "AR": "MO TN MS LA TX OK",
    "CA": "OR NV AZ", "CO": "WY NE KS OK NM UT", "CT": "NY MA RI", "DE": "MD PA NJ",
    "DC": "MD VA", "FL": "AL GA", "GA": "FL AL TN NC SC", "HI": "", "ID": "WA OR NV UT WY MT",
    "IL": "WI IA MO KY IN", "IN": "IL MI OH KY", "IA": "MN WI IL MO NE SD", "KS": "NE MO OK CO",
    "KY": "IL IN OH WV VA TN MO", "LA": "TX AR MS", "ME": "NH", "MD": "PA DE VA WV DC",
    "MA": "RI CT NY VT NH", "MI": "IN OH WI", "MN": "ND SD IA WI", "MS": "LA AR TN AL",
    "MO": "IA IL KY TN AR OK KS NE", "MT": "ID WY SD ND", "NE": "SD IA MO KS CO WY",
    "NV": "CA OR ID UT AZ", "NH": "ME MA VT", "NJ": "NY PA DE", "NM": "AZ CO OK TX",
    "NY": "VT MA CT NJ PA", "NC": "VA TN GA SC", "ND": "MN SD MT", "OH": "PA WV KY IN MI",
    "OK": "KS MO AR TX NM CO", "OR": "WA ID NV CA", "PA": "NY NJ DE MD WV OH", "RI": "CT MA",
    "SC": "NC GA", "SD": "ND MN IA NE WY MT", "TN": "KY VA NC GA AL MS AR MO", "TX": "NM OK AR LA",
    "UT": "ID WY CO AZ NV", "VT": "NH MA NY", "VA": "MD DC WV KY TN NC", "WA": "ID OR",
    "WV": "OH PA MD VA KY", "WI": "MI MN IA IL", "WY": "MT SD NE CO UT ID",
}
OTHER_STATES = {"AK": "WA HI OR", "HI": "CA AK WA"}

MONTHS_EN = ["January", "February", "March", "April", "May", "June", "July", "August",
             "September", "October", "November", "December"]

# The hand-written pages that carry the app's MobileApplication JSON-LD, between these markers
# just before </head>; the build writes what goes between them (see app_jsonld). /practice/ is
# generated and gets it in render_index_page.
APP_JSONLD_BEGIN = "<!-- BEGIN app-jsonld: generated by tools/build_state_pages.py; edit the generator, not this block -->"
APP_JSONLD_END = "<!-- END app-jsonld -->"
APP_JSONLD_PAGES = {
    "index.html": "en",
    "cdl/index.html": "en",
    "motorcycle/index.html": "en",
    "es/index.html": "es",
    "es/cdl/index.html": "es",
    "es/motocicleta/index.html": "es",
}

# Every state's bank has at least this many questions, in English and in Spanish; the build
# stops if a pack ever has fewer, so "230+" on the home page cannot go stale silently. (The state
# pages print each pack's exact count.)
QUESTION_FLOOR = 230

# The home page (hand-written) says "231–245 questions per state" and "1,500+ different questions
# in all". The build stops if the packs no longer bear that out, so those cannot go stale either.
HOME_PER_STATE = (231, 245)
HOME_DISTINCT_FLOOR = 1500

# Search engines show about this much of a meta description; every generated page stays within it.
DESCRIPTION_MAX = 155

CHOICE_LETTERS = ["A", "B", "C", "D"]

# The approved copy (owner's decision 2026-10-05). The hand-written pages use the same strings, and
# tools/check_site.py allows the trial and price strings only in exactly this form.
PILL_EN = "2026 EDITION"
TRIAL_EN = "Weekly plan or pay once"  # 2026-10-23: no free trial on sale (1.8.10)
GUARANTEE_NOTE_EN = ('Pass Guarantee: pass your knowledge test or get your money back. '
                     '<a href="/guarantee/">Conditions apply.</a>')
FOOTER_LINE_EN = "DriveDrill is an independent study app and is not affiliated with any state agency."

# The hero phone of a state page: a capture that names no state, except on California's own page.
PHONE_EN = ("/img/store/02-road-scenes-en",
            "A DriveDrill practice question with an animated road scene: an ambulance approaching "
            "an intersection. Watch the road situation, then answer.")
PHONE_EN_BY_STATE = {
    "CA": ("/img/store/05-mock-exam-en",
           "DriveDrill's California mock exam, question 16 of 46, with an optional timer."),
}

CHECK_SVG = ('<svg class="i" viewBox="0 0 24 24" aria-hidden="true">'
             '<path d="M5 12.5l4.2 4.2L19 7"/></svg>')
SEARCH_SVG = ('<svg class="i" viewBox="0 0 24 24" aria-hidden="true"><circle cx="11" cy="11" r="6.5"/>'
              '<path d="M20 20l-4.2-4.2"/></svg>')


def esc(value) -> str:
    return html.escape(str(value), quote=True)


def slugify(name: str) -> str:
    out = []
    for ch in name.lower():
        if ch.isalnum():
            out.append(ch)
        elif ch in (" ", "-", "_"):
            out.append("-")
        # anything else (periods, apostrophes, etc.) is dropped
    slug = "".join(out)
    while "--" in slug:
        slug = slug.replace("--", "-")
    return slug.strip("-")


# ------------------------------------------------------------------------------ content

def extract_content(ref: str) -> Path:
    """The app repo's Content folder at `ref`, extracted to a temporary folder (read-only)."""
    tmp = Path(tempfile.mkdtemp(prefix="drivedrill-content-"))
    tar = subprocess.run(
        ["git", "-C", str(APP_REPO), "archive", "--format=tar", ref, CONTENT_IN_REPO],
        check=True, capture_output=True,
    ).stdout
    with tarfile.open(fileobj=io.BytesIO(tar)) as tf:
        tf.extractall(tmp, filter="data")
    return tmp / CONTENT_IN_REPO


def load_states(content_dir: Path) -> list[dict]:
    states = []
    for fp in sorted(content_dir.glob("*.json")):
        if fp.name == "manifest.json":
            continue
        with fp.open(encoding="utf-8") as f:
            d = json.load(f)
        d["_slug"] = slugify(d["stateName"])
        states.append(d)
    states.sort(key=lambda d: d["stateName"])
    return states


def load_spanish_packs(content_dir: Path, states: list[dict]) -> dict[str, dict]:
    """{state code: Spanish pack}. Each must mirror its English pack exactly (ids, order,
    answer positions, pictures), which is how the app's own Spanish packs are built."""
    packs = {}
    for state in states:
        fp = content_dir / "es" / f"{state['stateCode'].lower()}.es.json"
        if not fp.is_file():
            raise SystemExit(f"Missing Spanish pack {fp}")
        with fp.open(encoding="utf-8") as f:
            es = json.load(f)
        en_q, es_q = state["questions"], es["questions"]
        if len(en_q) != len(es_q):
            raise SystemExit(f"{fp.name}: {len(es_q)} questions, English has {len(en_q)}")
        for a, b in zip(en_q, es_q):
            if (a["id"], a["answerIndex"], len(a["choices"]), "figure" in a) != (
                b["id"], b["answerIndex"], len(b["choices"]), "figure" in b
            ):
                raise SystemExit(f"{fp.name}: question {b['id']} does not mirror English {a['id']}")
        packs[state["stateCode"]] = es
    return packs


def check_question_floor(states: list[dict], es_packs: dict[str, dict]) -> None:
    low = [
        (s["stateCode"], len(s["questions"]), len(es_packs[s["stateCode"]]["questions"]))
        for s in states
        if min(len(s["questions"]), len(es_packs[s["stateCode"]]["questions"])) < QUESTION_FLOOR
    ]
    if low:
        raise SystemExit(f"These packs have fewer than {QUESTION_FLOOR} questions, so '{QUESTION_FLOOR}+' "
                         f"on the site would be false: {low}")


def check_home_counts(states: list[dict]) -> None:
    """The home page's per-state range and its total must still match the packs."""
    sizes = [len(s["questions"]) for s in states]
    distinct = len({q["id"] for s in states for q in s["questions"]})
    if (min(sizes), max(sizes)) != HOME_PER_STATE or distinct < HOME_DISTINCT_FLOOR:
        raise SystemExit(
            f"The packs now hold {min(sizes)}-{max(sizes)} questions per state and {distinct} different "
            f"questions; the home page (index.html) says {HOME_PER_STATE[0]}-{HOME_PER_STATE[1]} and "
            f"{HOME_DISTINCT_FLOOR:,}+. Update index.html and HOME_PER_STATE / HOME_DISTINCT_FLOOR.")


def pick_quiz_questions(state: dict) -> list[dict]:
    """Deterministic per-state selection: skips any question with a 'figure'
    key (no artwork on the site yet) and the self-referential '-exam-format'
    question, then mixes state-specific and general questions. Deterministic
    means reproducible on every run (seeded by state code), not identical
    across states.
    """
    code = state["stateCode"].lower()
    pool = [q for q in state["questions"] if "figure" not in q]
    pool = [q for q in pool if "exam-format" not in q["id"]]

    specific = sorted(
        (q for q in pool if q["id"].startswith(code + "-")), key=lambda q: q["id"]
    )
    general = sorted(
        (q for q in pool if not q["id"].startswith(code + "-")), key=lambda q: q["id"]
    )

    rng = random.Random("drivedrill-state-pages-" + code)
    n_specific = min(NUM_STATE_SPECIFIC, len(specific))
    n_general = min(NUM_QUIZ_QUESTIONS - n_specific, len(general))
    chosen = rng.sample(specific, n_specific) + rng.sample(general, n_general)

    if len(chosen) < NUM_QUIZ_QUESTIONS:  # defensive; not expected with current data
        chosen_ids = {q["id"] for q in chosen}
        rest = [q for q in pool if q["id"] not in chosen_ids]
        rng.shuffle(rest)
        chosen += rest[: NUM_QUIZ_QUESTIONS - len(chosen)]

    rng.shuffle(chosen)
    return chosen[:NUM_QUIZ_QUESTIONS]


def notes_state_the_format(notes: str, qcount: int) -> bool:
    """True when a state's notes already give its question count (California's under-18 and
    adult versions, say), so the computed format sentence would only repeat them."""
    return re.search(rf"(?<!\d){qcount}(?!\d)", notes) is not None


def format_intro(state: dict) -> str:
    """Format-facts paragraph. Only states real numbers when examFormatVerified
    is true; otherwise says the app mirrors the published format without
    presenting a question count or pass score as official.
    """
    name = state["stateName"]
    notes = (state.get("notes") or "").strip()

    if state.get("examFormatVerified"):
        qcount = state["examQuestionCount"]
        passc = state["passScore"]
        pct = round(100 * passc / qcount)
        computed = (
            f"{esc(subj(state))}'s permit knowledge test is {qcount} questions, and you need "
            f"{passc} correct (about {pct}%) to pass."
        )
        if not notes:
            return computed
        if notes_state_the_format(notes, qcount):
            return esc(notes)
        return f"{computed} {esc(notes)}"

    return (
        f"{unconfirmed_en(state)} The questions in this quiz are written in the style of "
        f"{esc(poss(state))} knowledge test, and {esc(poss(state))} official driver handbook "
        f"(linked below) is the source to study from."
    )


def short_name(state: dict) -> str:
    """The name in the headline: "DC" for the District of Columbia, else the state's name."""
    return "DC" if state["stateCode"] == "DC" else state["stateName"]


def noun(state: dict) -> str:
    """The name used as a noun inside a sentence: "the District of Columbia", "Texas"."""
    return "the District of Columbia" if state["stateCode"] == "DC" else state["stateName"]


def subj(state: dict) -> str:
    """The name opening a sentence: "The District of Columbia", "Texas"."""
    n = noun(state)
    return n[0].upper() + n[1:]


def poss(state: dict) -> str:
    """The possessive: "the District of Columbia's", "Texas's"."""
    return noun(state) + "'s"


def unconfirmed_en(state: dict) -> str:
    """What an unverified state's page says instead of a test length and pass score."""
    return (f"We couldn't confirm {esc(poss(state))} test length and pass score on an official page, "
            f"so we don't give numbers here.")


def facts_en(state: dict) -> tuple[str, str]:
    """The two facts of a state's hero, from the pack's own numbers. A format (mock exam length
    and pass score) only where the state's format is confirmed (examFormatVerified)."""
    count = f"{len(state['questions'])} questions"
    if state.get("examFormatVerified"):
        return count, f"mock exam of {state['examQuestionCount']}, pass with {state['passScore']}"
    return count, "full practice exam"


def fit(candidates: list[str], limit: int, what: str) -> str:
    """The first candidate that fits in `limit` characters; the build stops if none does."""
    for c in candidates:
        if len(c) <= limit:
            return c
    raise SystemExit(f"No {what} fits in {limit} characters: {candidates}")


def state_title(state: dict) -> str:
    name, short = state["stateName"], short_name(state)
    return fit([f"{name} Permit Practice Test 2026 (Free) | DriveDrill",
                f"{short} Permit Practice Test 2026 (Free) | DriveDrill",
                f"{name} Permit Practice Test 2026 (Free)",
                f"{short} Permit Practice Test 2026 (Free)"], TITLE_MAX, "title")


def meta_description(state: dict) -> str:
    n, k = len(state["questions"]), NUM_QUIZ_QUESTIONS
    name, short = state["stateName"], short_name(state)
    if state.get("examFormatVerified"):
        exam = f"{n} questions in the app, mock exam of {state['examQuestionCount']}, pass with {state['passScore']}."
    else:
        exam = f"{n} questions and full practice exams in the app."
    cands = []
    for s in (name, short):
        cands += [f"Free {s} permit practice test for 2026: {k} DMV-style questions, every answer explained. {exam}",
                  f"Free {s} permit practice test for 2026: {k} questions, every answer explained. {exam}",
                  f"Free {s} permit practice test: {k} questions, every answer explained. {exam}"]
    return fit(cands, DESCRIPTION_MAX, "description")


def topic_counts(state: dict) -> list[tuple[str, int]]:
    """(tag, number of the state's questions with that tag), in the app's topic order. A
    question can carry two tags, so the counts add up to more than the bank."""
    counts = {t: 0 for t in TOPIC_ORDER}
    for q in state["questions"]:
        for t in q.get("tags", []):
            if t in counts:
                counts[t] += 1
    return [(t, c) for t, c in counts.items() if c]


def top_topics(state: dict, n: int = 4) -> list[str]:
    """The n most-covered topic tags of a state's bank, most first (ties in the app's order)."""
    return [t for t, _ in sorted(topic_counts(state), key=lambda tc: -tc[1])[:n]]


def specific_count(state: dict) -> int:
    """How many questions of the bank were written for the state itself (id "<code>-...")."""
    code = state["stateCode"].lower()
    return sum(1 for q in state["questions"] if q["id"].startswith(code + "-"))


def check_neighbors(states: list[dict]) -> None:
    codes = {s["stateCode"] for s in states}
    if set(NEIGHBORS) != codes:
        raise SystemExit(f"NEIGHBORS does not list exactly the 51 states: {sorted(set(NEIGHBORS) ^ codes)}")
    for a, row in NEIGHBORS.items():
        for b in row.split():
            if a not in NEIGHBORS[b].split():
                raise SystemExit(f"NEIGHBORS is not symmetric: {a} lists {b}, {b} does not list {a}")
    for a, row in OTHER_STATES.items():
        if NEIGHBORS[a] or not set(row.split()) <= codes:
            raise SystemExit(f"OTHER_STATES[{a}] is wrong")


def related_states(state: dict, by_code: dict[str, dict]) -> tuple[bool, list[dict]]:
    """(True if these are bordering states, the states), sorted by name."""
    code = state["stateCode"]
    row = NEIGHBORS[code] or OTHER_STATES[code]
    return bool(NEIGHBORS[code]), sorted((by_code[c] for c in row.split()), key=lambda s: s["stateName"])


def long_date_en(iso: str) -> str:
    y, m, d = (int(x) for x in iso.split("-"))
    return f"{MONTHS_EN[m - 1]} {d}, {y}"


def spanish_test_en(state: dict) -> str:
    """Whether the state gives the knowledge test in Spanish, as the app's pack records it
    (spanishTest; the meaning of each value is the app's SpanishTest.Availability)."""
    name, agency = esc(state["stateName"]), esc(state["agency"])
    test = state.get("spanishTest") or {}
    a = test.get("knowledgeTestInSpanish")
    check = f" Languages can change, so check with the {name} {agency} before test day."
    if a == "yes":
        text = f"An official {name} source names Spanish for the knowledge test." + check
    elif a == "online":
        text = (f"{esc(subj(state))}'s official online knowledge test offers Spanish; the languages offered in "
                f"person are not published." + check)
    elif a == "no":
        since = test.get("englishOnlySince")
        text = (f"{esc(subj(state))} gives the knowledge test in English only, since {long_date_en(since)}."
                if since else f"{esc(subj(state))} gives the knowledge test in English only.")
    elif a == "unconfirmed":
        text = f"We found no official source that says {esc(noun(state))} offers the knowledge test in Spanish." + check
    else:
        raise SystemExit(f"{state['stateCode']}: unknown spanishTest value {a!r}")
    if test.get("sourceURL"):
        label = esc(test.get("source") or "Source")
        text += f' Source: <a href="{esc(test["sourceURL"])}">{label}</a>.'
    return text


# ------------------------------------------------------------------------------ structured data

def strip_tags(fragment: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", "", fragment)).strip()


def jsonld(obj: dict) -> str:
    """A JSON-LD block. '</' is escaped so no string can close the script element."""
    body = json.dumps(obj, ensure_ascii=False, indent=1).replace("</", "<\\/")
    return f'<script type="application/ld+json">\n{body}\n</script>'


def faq_jsonld(items: list[tuple[str, str]]) -> str:
    """FAQPage for the visible FAQ section: the same questions, and each answer as plain text."""
    return jsonld({
        "@context": "https://schema.org",
        "@type": "FAQPage",
        "mainEntity": [
            {"@type": "Question", "name": q,
             "acceptedAnswer": {"@type": "Answer", "text": strip_tags(a)}}
            for q, a in items
        ],
    })


def breadcrumb_jsonld(crumbs: list[tuple[str, str]]) -> str:
    return jsonld({
        "@context": "https://schema.org",
        "@type": "BreadcrumbList",
        "itemListElement": [
            {"@type": "ListItem", "position": i + 1, "name": name, "item": SITE_URL + path}
            for i, (name, path) in enumerate(crumbs)
        ],
    })


APP_DESCRIPTION = {
    "en": ("Permit practice tests for all 50 states and DC, with CDL and motorcycle practice, "
           "every answer explained, in English and Spanish."),
    "es": ("Práctica para el examen de permiso de manejo de los 50 estados y DC, con práctica de "
           "CDL y motocicleta y cada respuesta explicada, en inglés y en español."),
}


def app_jsonld(lang: str) -> str:
    """The iOS app as a MobileApplication. Deliberately no offers/price (the site prints prices
    only in the approved strings) and no aggregateRating or review (too few ratings, and Google
    does not want self-serving ratings on the seller's own pages); no person named."""
    store = store_links.APP_STORE
    return jsonld({
        "@context": "https://schema.org",
        "@type": "MobileApplication",
        "name": "DriveDrill",
        "operatingSystem": "iOS",
        "applicationCategory": "EducationalApplication",
        "description": APP_DESCRIPTION[lang],
        "inLanguage": ["en", "es"],
        "url": f"{SITE_URL}/" if lang == "en" else f"{SITE_URL}/es/",
        "installUrl": store,
        "sameAs": [store],
        "image": f"{SITE_URL}/apple-touch-icon.png",
    })


def website_jsonld() -> str:
    """The site's name for search results (home page only)."""
    return jsonld({"@context": "https://schema.org", "@type": "WebSite",
                   "name": "DriveDrill", "url": f"{SITE_URL}/"})


def inject_app_jsonld(site_root: Path) -> None:
    """Writes the app's JSON-LD (plus WebSite on the home page) between the markers of every
    page in APP_JSONLD_PAGES."""
    for rel, lang in APP_JSONLD_PAGES.items():
        path = site_root / rel
        with path.open(encoding="utf-8", newline="") as f:
            text = f.read()
        start, end = text.find(APP_JSONLD_BEGIN), text.find(APP_JSONLD_END)
        if start < 0 or end < start:
            raise SystemExit(f"{path}: the BEGIN/END app-jsonld markers are missing")
        nl = "\r\n" if "\r\n" in text else "\n"
        block = app_jsonld(lang) + ("\n" + website_jsonld() if rel == "index.html" else "")
        new = text[: start + len(APP_JSONLD_BEGIN)] + nl + block.replace("\n", nl) + nl + text[end:]
        if new != text:
            with path.open("w", encoding="utf-8", newline="") as f:
                f.write(new)


# ------------------------------------------------------------------------------ page parts

def alternates(en_path: str, es_path: str) -> dict[str, str]:
    """hreflang pair (plus x-default = English) for a page that has a twin."""
    return {
        "en": f"{SITE_URL}{en_path}",
        "es": f"{SITE_URL}{es_path}",
        "x-default": f"{SITE_URL}{en_path}",
    }


def render_head(title: str, description: str, canonical: str,
                hreflang: dict[str, str] | None = None, extra: str = "") -> str:
    """extra: JSON-LD blocks, written just before the stylesheet link."""
    if len(description) > DESCRIPTION_MAX:
        raise SystemExit(f"{canonical}: meta description is {len(description)} characters "
                         f"(max {DESCRIPTION_MAX}): {description}")
    if len(title) > TITLE_MAX:
        raise SystemExit(f"{canonical}: title is {len(title)} characters (max {TITLE_MAX}): {title}")
    extra = extra + "\n" if extra else ""
    alt = "".join(
        f'<link rel="alternate" hreflang="{lang}" href="{esc(url)}">\n'
        for lang, url in (hreflang or {}).items()
    )
    return f"""<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="apple-itunes-app" content="app-id=6813140105">
<meta name="color-scheme" content="light">
<title>{esc(title)}</title>
<meta name="description" content="{esc(description)}">
<link rel="canonical" href="{esc(canonical)}">
{alt}<link rel="icon" href="/favicon-32.png" sizes="32x32">
<link rel="apple-touch-icon" href="/apple-touch-icon.png">
<meta name="theme-color" content="#2163EB">
<meta property="og:type" content="website">
<meta property="og:site_name" content="DriveDrill">
<meta property="og:title" content="{esc(title)}">
<meta property="og:description" content="{esc(description)}">
<meta property="og:url" content="{esc(canonical)}">
<meta property="og:image" content="{SITE_URL}/og.png">
<meta property="og:image:width" content="1200">
<meta property="og:image:height" content="630">
<meta name="twitter:card" content="summary_large_image">
<meta name="twitter:title" content="{esc(title)}">
<meta name="twitter:description" content="{esc(description)}">
<meta name="twitter:image" content="{SITE_URL}/og.png">
{extra}<link rel="stylesheet" href="/style.css">
</head>"""


def render_header(es_href: str = "/es/") -> str:
    """The blue header of every English page; es_href is the page's Spanish twin."""
    return f"""<header class="top">
  <div class="wrap top-in">
    <a class="brand" href="/"><img src="/img/icon-192.png" alt="" width="32" height="32">DriveDrill</a>
    <nav class="nav" aria-label="Main">
      <a class="d-only" href="/#states">States</a>
      <a href="/cdl/">CDL</a>
      <a href="/motorcycle/">Motorcycle</a>
      <a class="d-only" href="/guarantee/">Pass Guarantee</a>
      <a class="lang" href="{es_href}" hreflang="es" lang="es" aria-label="Español"><span class="d-only">Español</span><span class="m-only">ES</span></a>
    </nav>
  </div>
</header>"""


def render_footer(es_href: str = "/es/") -> str:
    return f"""<footer class="foot">
  <div class="wrap">
    <div class="foot-top">
      <a class="brand" href="/"><img src="/img/icon-192.png" alt="" width="32" height="32">DriveDrill</a>
      <nav class="foot-nav" aria-label="Footer">
        <a href="/practice/">Practice tests</a>
        <a href="/guarantee/">Pass Guarantee</a>
        <a href="/privacy.html">Privacy</a>
        <a href="/terms.html">Terms</a>
        <a href="/support.html">Support</a>
        <a href="{es_href}" hreflang="es" lang="es">Español</a>
      </nav>
    </div>
    <p class="disclaimer-line">{FOOTER_LINE_EN}</p>
    <p class="copy">&copy; 2026 DriveDrill</p>
  </div>
</footer>"""


BADGE = {
    "en": ("/img/app-store-badge.svg", "Download on the App Store"),
    "es": ("/img/app-store-badge-es.svg", "Descárgalo en el App Store"),
}


def store_button(slot: str, lang: str = "en") -> str:
    """An App Store button: Apple's own badge artwork, in the page's language. Its href comes
    from tools/store_links.py and nowhere else."""
    src, alt = BADGE[lang]
    return (f'<a class="store-link appstore" data-store="{slot}" '
            f'href="{esc(store_links.STORE_LINKS[slot])}">'
            f'<img src="{src}" alt="{esc(alt)}" width="162" height="54"></a>')


def facts_line(first: str, second: str) -> str:
    """Two facts; on a phone they break between the facts, never inside one (and a line never
    starts with the dot: a no-break space ties it to the fact before it)."""
    return (f'<p class="facts-line"><span class="f">{esc(first)}</span>'
            f'<span class="dot br-m" aria-hidden="true">&nbsp;&middot;</span> '
            f'<span class="f bl-m">{esc(second)}</span></p>')


def render_hero(*, crumbs: str, pill: str, h1: str, facts: str, cta: str, phone: tuple[str, str]) -> str:
    """The blue hero of a state page (mockup A): the h1 sits inside <main>."""
    src, alt = phone
    return f"""  <section class="hero" aria-labelledby="hero-title">
    <div class="wrap hero-grid">
      <div class="hero-copy">
        {crumbs}
        <span class="pill">{pill}</span>
        <h1 id="hero-title" class="h1-long">{h1}</h1>
        {facts}
        <div class="cta">
          {cta}
        </div>
      </div>
      <div class="hero-art"><picture><source type="image/webp" srcset="{src}.webp"><img src="{src}.jpg" width="540" height="1173" alt="{esc(alt)}" decoding="async"></picture></div>
    </div>
  </section>"""


def render_question_block(q: dict, idx: int, qprefix: str = "Q") -> str:
    choices_html = []
    for i, choice in enumerate(q["choices"]):
        letter = CHOICE_LETTERS[i] if i < len(CHOICE_LETTERS) else str(i + 1)
        choices_html.append(
            f'      <button type="button" class="choice" data-i="{i}">'
            f'<span class="l">{letter}</span>{esc(choice)}</button>'
        )
    choices_block = "\n".join(choices_html)

    return f"""  <fieldset class="quiz-q" data-correct="{int(q['answerIndex'])}">
    <legend><span class="qnum">{qprefix}{idx}.</span> {esc(q['prompt'])}</legend>
    <div class="choices">
{choices_block}
    </div>
    <p class="explain" hidden><strong class="verdict"></strong><span class="explain-text"> {esc(q['explanation'])}</span></p>
  </fieldset>"""


# The words the quiz script shows, per language. The script itself is shared.
QUIZ_WORDS_EN = """  var T = {
    progress: function (a, c, t) {
      return a + ' of ' + t + ' answered' + (a > 0 ? ' \\u00b7 ' + c + ' correct so far' : '');
    },
    done: function (c, t) { return 'You got ' + c + ' of ' + t + ' correct.'; },
    right: 'Correct.',
    wrong: 'Not quite.'
  };"""

QUIZ_SCRIPT = """<script>
(function () {
__WORDS__
  var quiz = document.getElementById('quiz');
  var scoreEl = document.getElementById('score');
  if (!quiz || !scoreEl) return;
  var questions = quiz.querySelectorAll('.quiz-q');
  var total = questions.length;
  var answered = 0, correct = 0;

  function updateScore() {
    scoreEl.textContent = answered < total ? T.progress(answered, correct, total) : T.done(correct, total);
  }
  updateScore();

  quiz.addEventListener('click', function (e) {
    var btn = e.target.closest ? e.target.closest('.choice') : null;
    if (!btn || !quiz.contains(btn)) return;
    var q = btn.closest('.quiz-q');
    if (!q || q.classList.contains('answered')) return;
    q.classList.add('answered');

    var correctIdx = parseInt(q.getAttribute('data-correct'), 10);
    var pickedIdx = parseInt(btn.getAttribute('data-i'), 10);
    var buttons = q.querySelectorAll('.choice');
    for (var i = 0; i < buttons.length; i++) { buttons[i].disabled = true; }

    var isRight = pickedIdx === correctIdx;
    btn.classList.add(isRight ? 'right' : 'wrong');
    if (!isRight) {
      var correctBtn = q.querySelector('.choice[data-i="' + correctIdx + '"]');
      if (correctBtn) correctBtn.classList.add('right');
    }

    var explain = q.querySelector('.explain');
    var verdict = explain.querySelector('.verdict');
    verdict.textContent = isRight ? T.right : T.wrong;
    explain.hidden = false;

    answered++;
    if (isRight) correct++;
    updateScore();
  });
})();
</script>"""


def quiz_script(words: str) -> str:
    return QUIZ_SCRIPT.replace("__WORDS__", words)


# The "Find your state" box on /practice/, the home page and /es/ (the two home pages carry a
# copy of this exact script). It matches state names with or without accents, Enter opens the
# first match, and Enter in an empty box does nothing.
FILTER_SCRIPT = """<script>
(function () {
  var q = document.getElementById('state-q');
  var list = document.getElementById('state-list');
  var none = document.getElementById('no-match');
  if (!q || !list) return;
  var items = list.getElementsByTagName('li');
  function fold(s) {
    s = s.toLowerCase();
    return s.normalize ? s.normalize('NFD').replace(/[\\u0300-\\u036f]/g, '') : s;
  }
  function label(li) { return (li.querySelector('strong') || li).textContent; }
  q.addEventListener('input', function () {
    var v = fold(q.value.trim()), shown = 0;
    for (var i = 0; i < items.length; i++) {
      var hit = !v || fold(label(items[i])).indexOf(v) !== -1;
      items[i].hidden = !hit;
      if (hit) shown++;
    }
    if (none) none.hidden = shown !== 0;
  });
  q.addEventListener('keydown', function (e) {
    if (e.key !== 'Enter') return;
    e.preventDefault();
    if (!q.value.trim()) return;
    for (var i = 0; i < items.length; i++) {
      if (!items[i].hidden) { window.location.href = items[i].querySelector('a').href; return; }
    }
  });
})();
</script>"""


# ------------------------------------------------------------------------------ English pages

def topics_phrase(tags: list[str], labels: dict[str, str], conj: str) -> str:
    """"road signs, right of way, sharing the road and licensing and penalties"."""
    words = [labels[t].lower().replace(" & ", f" {conj} ") for t in tags]
    return ", ".join(words[:-1]) + f" {conj} " + words[-1] if len(words) > 1 else "".join(words)


def chip_list(links: list[tuple[str, str]], lang: str | None = None) -> str:
    attr = f' hreflang="{lang}"' if lang else ""
    rows = "\n".join(f'        <li><a href="{href}"{attr}>{esc(label)}</a></li>' for href, label in links)
    return f'      <ul class="state-list related">\n{rows}\n      </ul>'


def faq_html(items: list[tuple[str, str]]) -> str:
    return "\n".join(f"      <h3>{esc(q)}</h3>\n      <p>{a}</p>" for q, a in items)


def faq_en(state: dict) -> list[tuple[str, str]]:
    """The FAQ of an English state page: (question, answer HTML). Every number comes from the
    pack, and only where its format is verified."""
    name, agency, slug = esc(state["stateName"]), esc(state["agency"]), state["_slug"]
    n, k = len(state["questions"]), NUM_QUIZ_QUESTIONS
    handbook = f'<a href="{esc(state["handbookURL"])}">{name} {agency} driver handbook</a>'
    if state.get("examFormatVerified"):
        count = format_intro(state)
        mock = f"a {state['examQuestionCount']}-question mock exam"
    else:
        count = f"{unconfirmed_en(state)} The {handbook} is the source to study from."
        mock = "full practice exams"
    topics = topics_phrase(top_topics(state), TOPIC_EN, "and")
    return [
        (f"How many questions are on the {state['stateName']} permit test?", count),
        (f"Can I take the {state['stateName']} permit test in Spanish?",
         f'{spanish_test_en(state)} DriveDrill\'s {name} questions are also <a href="/es/{slug}/" '
         f'hreflang="es">in Spanish</a>.'),
        (f"Are these the real {state['stateName']} test questions?",
         f"No. They are practice questions written in the style of {esc(poss(state))} knowledge test, not official "
         f"test questions. DriveDrill is an independent study app and is not affiliated with the {name} "
         f"{agency} or any other state agency."),
        (f"Is this {state['stateName']} permit practice test free?",
         f"Yes. The {k} sample questions on this page are free, with every answer explained and no "
         f"sign-up. The DriveDrill iPhone app has all {n} {name} questions and {mock}."),
        (f"What should I study for the {state['stateName']} permit test?",
         f"The official {handbook} is the authoritative source for current rules. DriveDrill's {name} "
         f"questions cover {topics}, among other topics."),
        ("Does DriveDrill have CDL and motorcycle practice tests?",
         'Yes. The app also has practice for the <a href="/cdl/">CDL knowledge tests</a> and the '
         '<a href="/motorcycle/">motorcycle permit test</a>. Parents paying for a teen can read '
         '<a href="/parents/">DriveDrill for parents</a>.'),
    ]


def about_en(state: dict, sample_specific: int) -> str:
    name, agency = esc(state["stateName"]), esc(state["agency"])
    n, spec = len(state["questions"]), specific_count(state)
    if state.get("examFormatVerified"):
        fmt = format_intro(state)
        app = (f"{n} {name} questions, {spec} of them written for {esc(poss(state))} own rules, and a mock exam "
               f"of {state['examQuestionCount']} that you pass with {state['passScore']}.")
    else:
        fmt = unconfirmed_en(state)
        app = f"{n} {name} questions, {spec} of them written for {esc(poss(state))} own rules, and full practice exams."
    topics = "\n".join(f"        <li>{esc(TOPIC_EN[t])}: {c}</li>" for t, c in topic_counts(state))
    return f"""      <h2 class="sec-title" id="about-title">About the {name} knowledge test</h2>
      <ul>
        <li><strong>Format:</strong> {fmt}</li>
        <li><strong>In Spanish:</strong> {spanish_test_en(state)}</li>
        <li><strong>What to study:</strong> the official <a href="{esc(state['handbookURL'])}">{name} {agency} driver handbook</a>, always the authoritative reference for current rules.</li>
        <li><strong>In DriveDrill:</strong> {app}</li>
      </ul>
      <p>The {NUM_QUIZ_QUESTIONS} questions above include {sample_specific} on {name}-specific rules. Topics in DriveDrill's {name} bank (questions per topic; a question can cover two):</p>
      <ul class="topics">
{topics}
      </ul>"""


def render_state_page(state: dict, questions: list[dict], by_code: dict[str, dict]) -> str:
    name = state["stateName"]
    slug = state["_slug"]
    agency = state["agency"]
    es_name = state["_es_name"]
    n = len(state["questions"])
    code = state["stateCode"].lower()
    sample_specific = sum(1 for q in questions if q["id"].startswith(code + "-"))

    faq = faq_en(state)
    title = state_title(state)
    description = meta_description(state)
    canonical = f"{SITE_URL}/practice/{slug}/"
    head = render_head(title, description, canonical,
                       alternates(f"/practice/{slug}/", f"/es/{slug}/"),
                       extra=faq_jsonld(faq) + "\n" + breadcrumb_jsonld(
                           [("DriveDrill", "/"), ("Permit practice tests", "/practice/"),
                            (f"{name} permit practice test", f"/practice/{slug}/")]))

    bordering, related = related_states(state, by_code)
    related_title = (f"Practice tests for states near {esc(short_name(state))}" if bordering
                     else "More state practice tests")
    related_html = chip_list([(f"/practice/{s['_slug']}/", f"{s['stateName']} permit practice test")
                              for s in related] + [("/practice/", "All 50 states + DC")])
    more_html = chip_list([("/cdl/", "CDL practice test"), ("/motorcycle/", "Motorcycle permit practice test"),
                           ("/parents/", "For parents"), ("/guarantee/", "Pass Guarantee")])

    question_blocks = "\n".join(
        render_question_block(q, i + 1) for i, q in enumerate(questions)
    )
    if state.get("examFormatVerified"):
        then = f"Then take the mock exam of {state['examQuestionCount']}, pass with {state['passScore']}."
    else:
        then = "Then take a full practice exam."

    hero = render_hero(
        crumbs=(f'<p class="crumbs"><a href="/practice/">&larr; All 50 states + DC</a>'
                f'<a href="/es/{slug}/" hreflang="es" lang="es">{esc(es_name)} en español</a></p>'),
        pill=PILL_EN,
        h1=f'{esc(short_name(state))} permit practice test <span class="hl">2026</span>',
        facts=facts_line(*facts_en(state)),
        cta=(f'{store_button("car")}\n'
             f'          <p class="trial-note">{CHECK_SVG}{TRIAL_EN}</p>\n'
             f'          <p class="g-note">{GUARANTEE_NOTE_EN}</p>'),
        phone=PHONE_EN_BY_STATE.get(state["stateCode"], PHONE_EN),
    )

    return f"""<!DOCTYPE html>
<html lang="en">
{head}
<body>
{render_header(f"/es/{slug}/")}

<main>
{hero}

  <section class="sec" id="sample" aria-labelledby="sample-title">
    <div class="wrap quiz-wrap">
      <h2 class="sec-title" id="sample-title">Free {esc(name)} permit practice test: {len(questions)} questions</h2>
      <p class="sec-sub">{len(questions)} sample questions from DriveDrill's {n}-question {esc(name)} bank. Pick an answer to see if you got it right, and why.</p>

      <p class="score-readout" id="score" aria-live="polite">0 of {len(questions)} answered</p>

      <div class="quiz" id="quiz">
{question_blocks}
      </div>

      <div class="endcard">
        <h2>Practice all {n} {esc(name)} questions in the app.</h2>
        <p>{then}</p>
        <p class="cta">{store_button("car")}</p>
        <p class="trial-note">{CHECK_SVG}{TRIAL_EN}</p>
      </div>
    </div>
  </section>

  <section class="sec sec-alt" id="about" aria-labelledby="about-title">
    <div class="wrap prose">
{about_en(state, sample_specific)}
    </div>
  </section>

  <section class="sec" id="faq" aria-labelledby="faq-title">
    <div class="wrap prose faq">
      <h2 class="sec-title" id="faq-title">{esc(name)} permit test: questions and answers</h2>
{faq_html(faq)}
    </div>
  </section>

  <section class="sec sec-alt" id="more" aria-labelledby="related-title">
    <div class="wrap prose">
      <h2 class="sec-title" id="related-title">{related_title}</h2>
{related_html}
      <h2 class="sec-title" id="more-title">More DriveDrill practice</h2>
{more_html}
      <div class="after-quiz">
        <p>
          Official source: <a href="{esc(state['handbookURL'])}">{esc(name)} {esc(agency)} driver handbook</a>
          &mdash; always the authoritative reference for current rules.
        </p>
        <p class="lang-line" lang="es">¿Prefieres estudiar en español? <a href="/es/{slug}/" hreflang="es">{esc(es_name)} en español</a></p>
        <p class="disclaimer">
          These practice questions are written in the style of {esc(poss(state))} knowledge test and
          are not official test questions. DriveDrill is an independent study app and is not
          affiliated with, endorsed by, or connected to the {esc(name)} {esc(agency)} or any other
          state agency.
        </p>
      </div>
    </div>
  </section>
</main>

{render_footer(f"/es/{slug}/")}
{quiz_script(QUIZ_WORDS_EN)}
</body>
</html>
"""


def render_index_page(all_states: list[dict]) -> str:
    k = NUM_QUIZ_QUESTIONS
    title = "Free DMV Permit Practice Test 2026: All 50 States + DC"
    description = (
        f"Free DMV permit practice tests for all 50 states and DC: {k} sample questions per state, "
        f"every answer explained, no sign-up. Pick your state."
    )
    canonical = f"{SITE_URL}/practice/"
    extra = app_jsonld("en") + "\n" + breadcrumb_jsonld(
        [("DriveDrill", "/"), ("Permit practice tests", "/practice/")])

    items = []
    for state in all_states:
        name = state["stateName"]
        slug = state["_slug"]
        first, second = facts_en(state)
        items.append(
            f'        <li><a href="/practice/{slug}/"><strong>{esc(name)}</strong>'
            f"<span>{esc(first)} &middot; {esc(second)}</span></a></li>"
        )
    items_html = "\n".join(items)

    return f"""<!DOCTYPE html>
<html lang="en">
{render_head(title, description, canonical, extra=extra)}
<body>
{render_header("/es/#estados")}

<main>
  <section class="hero hero-doc" aria-labelledby="hero-title">
    <div class="wrap hero-grid">
      <div class="hero-copy">
        <span class="pill">{PILL_EN}</span>
        <h1 id="hero-title">Free DMV permit practice tests for all 50 states + DC</h1>
        <p class="sub">Pick your state for a free {k}-question permit practice test, with every answer explained.</p>
        <div class="cta">
          {store_button("car")}
          <p class="trial-note">{CHECK_SVG}{TRIAL_EN}</p>
        </div>
      </div>
    </div>
  </section>

  <section class="sec" aria-label="States">
    <div class="wrap">
      <div class="lede prose">
        Each page below has {k} sample questions with instant answers and explanations, a short
        guide to the state's knowledge test, and answers to common questions. Where a
        state publishes its knowledge-test format on an official page we could confirm, the page
        gives the mock exam's length and pass score; otherwise it says full practice exam and
        points to the state's official handbook instead of guessing at numbers.
      </div>

      <p class="lang-switch" lang="es">¿Prefieres estudiar en español? <a href="/es/#estados" hreflang="es">Los 51 exámenes de práctica en español</a></p>

      <label class="filter">{SEARCH_SVG}<span class="sr">Find your state</span><input id="state-q" type="search" placeholder="Find your state" autocomplete="off"></label>
      <ul class="state-cards" id="state-list">
{items_html}
      </ul>
      <p class="no-match" id="no-match" hidden>No state matches. Try another spelling.</p>

      <p class="disclaimer">
        DriveDrill is an independent study app. It is not affiliated with, endorsed by, or
        connected to any state motor vehicle agency. Practice questions are written in the style
        of each state's knowledge test and are not official test questions.
      </p>
    </div>
  </section>
</main>

{render_footer("/es/#estados")}
{FILTER_SCRIPT}
</body>
</html>
"""


# ------------------------------------------------------------------------------ sitemap

# Hand-written pages, in the order they appear in the sitemap.
STATIC_PAGES = [
    ("/", "1.0"),
    ("/practice/", "0.9"),
    ("/cdl/", "0.9"),
    ("/motorcycle/", "0.9"),
    ("/es/", "0.9"),
    ("/es/cdl/", "0.8"),
    ("/es/motocicleta/", "0.8"),
    ("/parents/", "0.8"),            # 2026-10-27: the parents page and its Spanish twin
    ("/es/padres/", "0.8"),
    ("/guarantee/", "0.5"),
    ("/es/garantia/", "0.5"),
]
# /unlock.html is not listed: it is noindex (the web-purchase unlock step), and a sitemap should
# only list pages meant for search results. tools/check_site.py fails on a noindex page in it.
LEGAL_PAGES = ["/privacy.html", "/terms.html", "/support.html"]

# Pages whose sitemap entry also lists its hreflang pair (en, es, x-default = en): the same
# set the page's own <head> declares. tools/check_site.py fails if the two ever disagree.
SITEMAP_HREFLANG = {
    "/guarantee/": alternates("/guarantee/", "/es/garantia/"),
    "/es/garantia/": alternates("/guarantee/", "/es/garantia/"),
    "/parents/": alternates("/parents/", "/es/padres/"),
    "/es/padres/": alternates("/parents/", "/es/padres/"),
}


def file_of(url_path: str, site_root: Path = SITE_ROOT) -> Path:
    return site_root / (url_path.lstrip("/") + ("index.html" if url_path.endswith("/") else ""))


def lastmod(url_path: str, site_root: Path = SITE_ROOT) -> str:
    """The day a page last changed: today if its file differs from the last commit (or is new),
    else the date of the last commit that changed it. tools/check_site.py uses the same rule."""
    rel = file_of(url_path, site_root).relative_to(site_root).as_posix()
    git = ["git", "-C", str(site_root)]
    try:
        dirty = subprocess.run(git + ["status", "--porcelain", "--", rel],
                               check=True, capture_output=True, text=True).stdout.strip()
        if dirty:
            return TODAY
        day = subprocess.run(git + ["log", "-1", "--format=%cs", "--", rel],
                             check=True, capture_output=True, text=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return TODAY
    return day or TODAY


def render_sitemap(all_states: list[dict]) -> str:
    urls = [(path, prio) for path, prio in STATIC_PAGES]
    for state in all_states:
        urls.append((f"/practice/{state['_slug']}/", "0.8"))
    for state in all_states:
        urls.append((f"/es/{state['_slug']}/", "0.8"))
    urls += [(path, "0.3") for path in LEGAL_PAGES]

    def entry(path: str, priority: str) -> str:
        # Extension elements (xhtml:link) come after <priority>, as the sitemap schema orders them.
        alts = "".join(
            f'    <xhtml:link rel="alternate" hreflang="{lang}" href="{esc(url)}"/>\n'
            for lang, url in SITEMAP_HREFLANG.get(path, {}).items()
        )
        return (f"  <url>\n    <loc>{esc(SITE_URL + path)}</loc>\n    <lastmod>{lastmod(path)}</lastmod>\n"
                f"    <priority>{priority}</priority>\n{alts}  </url>")

    entries = "\n".join(entry(path, priority) for path, priority in urls)
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"'
        ' xmlns:xhtml="http://www.w3.org/1999/xhtml">\n'
        f"{entries}\n"
        "</urlset>\n"
    )


# ------------------------------------------------------------------------------ main

def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    src = parser.add_mutually_exclusive_group()
    src.add_argument("--app-ref", default=None, help=f"app repo commit to read the packs from (default {APP_REF})")
    src.add_argument("--content", type=Path, default=None, help="an extracted Content folder instead")
    parser.add_argument("--no-check", action="store_true", help="skip tools/check_site.py at the end")
    args = parser.parse_args()

    if args.content:
        content_dir = args.content
        source = str(content_dir)
    else:
        ref = args.app_ref or APP_REF
        content_dir = extract_content(ref)
        source = f"{APP_REPO} @ {ref}"
    if not content_dir.is_dir():
        raise SystemExit(f"Content dir not found: {content_dir}")

    all_states = load_states(content_dir)
    if len(all_states) != 51:
        raise SystemExit(f"Expected 51 state packs, found {len(all_states)}")
    es_packs = load_spanish_packs(content_dir, all_states)
    check_question_floor(all_states, es_packs)
    check_home_counts(all_states)
    for state in all_states:
        state["_es_name"] = es_packs[state["stateCode"]]["stateName"]  # "Nueva York en español"
    check_neighbors(all_states)
    by_code = {s["stateCode"]: s for s in all_states}

    import build_es_pages  # Spanish templates; imports this module's helpers

    practice_dir = SITE_ROOT / "practice"
    practice_dir.mkdir(exist_ok=True)

    verified_count = 0
    for state in all_states:
        questions = pick_quiz_questions(state)
        if len(questions) != NUM_QUIZ_QUESTIONS:
            raise SystemExit(f"{state['stateCode']}: only {len(questions)} sample questions")
        state["_quiz_ids"] = [q["id"] for q in questions]
        page_dir = practice_dir / state["_slug"]
        page_dir.mkdir(parents=True, exist_ok=True)
        (page_dir / "index.html").write_text(render_state_page(state, questions, by_code), encoding="utf-8")
        if state.get("examFormatVerified"):
            verified_count += 1

    (practice_dir / "index.html").write_text(render_index_page(all_states), encoding="utf-8")
    es_written = build_es_pages.build(SITE_ROOT, all_states, es_packs, by_code)
    inject_app_jsonld(SITE_ROOT)
    changed = store_links.apply_store_links(SITE_ROOT, quiet=True)
    # Last, so each page's lastmod sees every change this build made.
    (SITE_ROOT / "sitemap.xml").write_text(render_sitemap(all_states), encoding="utf-8")

    print(f"Read the question packs from {source}")
    print(f"Generated {len(all_states)} English state pages under {practice_dir}")
    print(f"  examFormatVerified numbers shown: {verified_count} states")
    print(f"  unverified (generic copy, no numbers): {len(all_states) - verified_count} states")
    print(f"Generated {es_written} Spanish state pages under {SITE_ROOT / 'es'} and the /es/ state list")
    print(f"Wrote {practice_dir / 'index.html'} and {SITE_ROOT / 'sitemap.xml'}")
    print(f"Store links: {changed} href(s) brought in line with tools/store_links.py")

    if not args.no_check:
        import check_site
        sys.exit(check_site.main([]))


if __name__ == "__main__":
    main()
