#!/usr/bin/env python3
"""
Builds the generated pages of drivedrill.app from DriveDrill's own question packs:

  practice/<state-slug>/index.html   English: a 10-question sample quiz per state (51)
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
An English state page and its Spanish twin always show the same 10 questions.

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
# Version 1.6.1 ("subscription access audit"), live on the App Store on 2026-10-03. The question
# packs are byte-identical through 1.7 (66c7c6f) and 1.8 (06efb1a).
APP_REF = "1d31d68"
CONTENT_IN_REPO = "DriveDrill/Resources/Content"

APP_STORE_URL = store_links.STORE_LINKS["car"]
SITE_URL = "https://drivedrill.app"
TODAY = date.today().isoformat()

NUM_QUIZ_QUESTIONS = 10
NUM_STATE_SPECIFIC = 4  # of the 10, how many must be state-specific (id "<code>-...")

# Every state's bank has at least this many questions, in English and in Spanish; the build
# stops if a pack ever has fewer, so "más de 230" on the Spanish state pages cannot go stale silently.
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
TRIAL_EN = "3-day free trial on weekly plans"
GUARANTEE_NOTE_EN = ('Pass Guarantee: pass your knowledge test or get your money back. '
                     '<a href="/guarantee/">Conditions apply.</a>')
FOOTER_LINE_EN = "DriveDrill is an independent study app and is not affiliated with any state agency."

# The hero phone of a state page: a capture that names no state, except on California's own page.
PHONE_EN = ("/img/phone-practice.jpg",
            "A DriveDrill practice question about an ambulance coming up to an intersection, "
            "with a picture of the scene.")
PHONE_EN_BY_STATE = {
    "CA": ("/img/phone-exam-ca.jpg",
           "DriveDrill's California mock exam, question 16 of 46: a railroad crossing question "
           "with a picture of the crossing."),
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
            f"{esc(name)}'s permit knowledge test is {qcount} questions, and you need "
            f"{passc} correct (about {pct}%) to pass."
        )
        if not notes:
            return computed
        if notes_state_the_format(notes, qcount):
            return esc(notes)
        return f"{computed} {esc(notes)}"

    return (
        f"{esc(name)} doesn't publish its knowledge-test length and pass score on an "
        f"official page we could confirm, so we don't state numbers here. The questions in "
        f"this quiz are written in the style of {esc(name)}'s knowledge test, and "
        f"{esc(name)}'s official driver handbook (linked below) is the source to study from."
    )


def short_name(state: dict) -> str:
    """The name in the headline: "DC" for the District of Columbia, else the state's name."""
    return "DC" if state["stateCode"] == "DC" else state["stateName"]


def facts_en(state: dict) -> tuple[str, str]:
    """The two facts of a state's hero, from the pack's own numbers. A format (mock exam length
    and pass score) only where the state's format is confirmed (examFormatVerified)."""
    count = f"{len(state['questions'])} questions"
    if state.get("examFormatVerified"):
        return count, f"mock exam of {state['examQuestionCount']}, pass with {state['passScore']}"
    return count, "full practice exam"


def meta_description(state: dict) -> str:
    n = len(state["questions"])
    if state.get("examFormatVerified"):
        exam = f"{n} questions, mock exam of {state['examQuestionCount']}, pass with {state['passScore']}."
    else:
        exam = f"{n} questions and a full practice exam."
    return (f"Pass the {short_name(state)} permit test first try. {exam} "
            f"Every answer explained. Try 10 sample questions here.")


# ------------------------------------------------------------------------------ page parts

def alternates(en_path: str, es_path: str) -> dict[str, str]:
    """hreflang pair (plus x-default = English) for a page that has a twin."""
    return {
        "en": f"{SITE_URL}{en_path}",
        "es": f"{SITE_URL}{es_path}",
        "x-default": f"{SITE_URL}{en_path}",
    }


def render_head(title: str, description: str, canonical: str,
                hreflang: dict[str, str] | None = None) -> str:
    if len(description) > DESCRIPTION_MAX:
        raise SystemExit(f"{canonical}: meta description is {len(description)} characters "
                         f"(max {DESCRIPTION_MAX}): {description}")
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
<link rel="stylesheet" href="/style.css">
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
      <div class="hero-phone">
        <div class="device"><img src="{src}" width="760" height="1651" alt="{esc(alt)}"></div>
      </div>
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

def render_state_page(state: dict, questions: list[dict]) -> str:
    name = state["stateName"]
    slug = state["_slug"]
    agency = state["agency"]
    es_name = state["_es_name"]
    n = len(state["questions"])

    title = f"Free {name} Permit Practice Test (2026) | DriveDrill"
    description = meta_description(state)
    canonical = f"{SITE_URL}/practice/{slug}/"
    head = render_head(title, description, canonical,
                       alternates(f"/practice/{slug}/", f"/es/{slug}/"))

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
        h1=f'Pass the {esc(short_name(state))} permit test <span class="hl">first try.</span>',
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
      <div class="lede">{format_intro(state)}</div>

      <h2 class="sec-title" id="sample-title">{esc(name)} sample questions</h2>
      <p class="sec-sub">Pick an answer to see if you got it right. Every answer explained.</p>

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

      <div class="after-quiz">
        <p>
          Official source: <a href="{esc(state['handbookURL'])}">{esc(name)} {esc(agency)} driver handbook</a>
          &mdash; always the authoritative reference for current rules.
        </p>
        <p class="lang-line" lang="es">¿Prefieres estudiar en español? <a href="/es/{slug}/" hreflang="es">{esc(es_name)} en español</a></p>
        <p class="disclaimer">
          These practice questions are written in the style of {esc(name)}'s knowledge test and
          are not official test questions.
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
    title = "Free DMV Permit Practice Tests — All 50 States + DC | DriveDrill"
    description = (
        "Free 10-question permit practice tests for all 50 states and DC, with instant answers "
        "and explanations. Pick your state."
    )
    canonical = f"{SITE_URL}/practice/"

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
{render_head(title, description, canonical)}
<body>
{render_header("/es/#estados")}

<main>
  <section class="hero hero-doc" aria-labelledby="hero-title">
    <div class="wrap hero-grid">
      <div class="hero-copy">
        <span class="pill">{PILL_EN}</span>
        <h1 id="hero-title">Free practice tests for all 50 states + DC</h1>
        <p class="sub">Pick your state for a free 10-question sample of DriveDrill's permit practice test.</p>
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
        Each page below has 10 sample questions with instant answers and explanations. Where a
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
    ("/guarantee/", "0.5"),
    ("/es/garantia/", "0.5"),
]
LEGAL_PAGES = ["/privacy.html", "/terms.html", "/support.html"]

# Pages whose sitemap entry also lists its hreflang pair (en, es, x-default = en): the same
# set the page's own <head> declares. tools/check_site.py fails if the two ever disagree.
SITEMAP_HREFLANG = {
    "/guarantee/": alternates("/guarantee/", "/es/garantia/"),
    "/es/garantia/": alternates("/guarantee/", "/es/garantia/"),
}


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
        return (f"  <url>\n    <loc>{esc(SITE_URL + path)}</loc>\n    <lastmod>{TODAY}</lastmod>\n"
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

    import build_es_pages  # Spanish templates; imports this module's helpers

    practice_dir = SITE_ROOT / "practice"
    practice_dir.mkdir(exist_ok=True)

    verified_count = 0
    for state in all_states:
        questions = pick_quiz_questions(state)
        state["_quiz_ids"] = [q["id"] for q in questions]
        page_dir = practice_dir / state["_slug"]
        page_dir.mkdir(parents=True, exist_ok=True)
        (page_dir / "index.html").write_text(render_state_page(state, questions), encoding="utf-8")
        if state.get("examFormatVerified"):
            verified_count += 1

    (practice_dir / "index.html").write_text(render_index_page(all_states), encoding="utf-8")
    es_written = build_es_pages.build(SITE_ROOT, all_states, es_packs)
    (SITE_ROOT / "sitemap.xml").write_text(render_sitemap(all_states), encoding="utf-8")
    changed = store_links.apply_store_links(SITE_ROOT, quiet=True)

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
