#!/usr/bin/env python3
"""
Builds the generated pages of drivedrill.app from DriveDrill's own question packs:

  practice/<state-slug>/index.html   English: a 10-question sample quiz per state (51)
  practice/index.html                English: the list of all 51
  es/<state-slug>/index.html         Spanish twin of every state page (51), from the
                                     Spanish packs; templates in tools/build_es_pages.py
  es/index.html                      ONLY the state list between its BEGIN/END markers
  sitemap.xml                        every page on the site

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
# stops if a pack ever has fewer, so "230+" on the site cannot go stale silently.
QUESTION_FLOOR = 230

CHOICE_LETTERS = ["A", "B", "C", "D"]


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


def meta_description(state: dict) -> str:
    name = state["stateName"]
    if state.get("examFormatVerified"):
        qcount = state["examQuestionCount"]
        return (
            f"Free {name} permit practice test: 10 questions with instant answers and "
            f"explanations, matching {name}'s real {qcount}-question DMV format."
        )
    return (
        f"Free {name} permit practice test: 10 questions with instant answers and "
        f"explanations, written in the style of {name}'s DMV knowledge test."
    )


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
    alt = "".join(
        f'<link rel="alternate" hreflang="{lang}" href="{esc(url)}">\n'
        for lang, url in (hreflang or {}).items()
    )
    return f"""<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="apple-itunes-app" content="app-id=6813140105">
<title>{esc(title)}</title>
<meta name="description" content="{esc(description)}">
<link rel="canonical" href="{esc(canonical)}">
{alt}<link rel="icon" href="/favicon-32.png" sizes="32x32">
<link rel="apple-touch-icon" href="/apple-touch-icon.png">
<meta name="theme-color" content="#101216">
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


NAV = """<nav>
      <a href="/practice/">Practice Tests</a>
      <a href="/privacy.html">Privacy</a>
      <a href="/terms.html">Terms</a>
      <a href="/support.html">Support</a>
    </nav>"""


def render_header() -> str:
    return f"""<header>
  <div class="wrap">
    <a class="logo" href="/index.html">Drive<span>Drill</span></a>
    {NAV}
  </div>
</header>"""


def render_footer() -> str:
    return """<footer>
  <div class="wrap">
    &copy; 2026 DriveDrill ·
    <a href="/practice/">Practice Tests</a> ·
    <a href="/privacy.html">Privacy</a> ·
    <a href="/terms.html">Terms</a> ·
    <a href="/support.html">Support</a>
  </div>
</footer>"""


def store_button(slot: str, label: str) -> str:
    """An App Store button. Its href comes from tools/store_links.py and nowhere else."""
    return (f'<a class="button store-link" data-store="{slot}" '
            f'href="{esc(store_links.STORE_LINKS[slot])}">{esc(label)}</a>')


def render_question_block(q: dict, idx: int, qprefix: str = "Q") -> str:
    choices_html = []
    for i, choice in enumerate(q["choices"]):
        letter = CHOICE_LETTERS[i] if i < len(CHOICE_LETTERS) else str(i + 1)
        choices_html.append(
            f'      <button type="button" class="choice" data-i="{i}">'
            f'<span class="choice-letter">{letter}</span> {esc(choice)}</button>'
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


# ------------------------------------------------------------------------------ English pages

def render_state_page(state: dict, questions: list[dict]) -> str:
    name = state["stateName"]
    slug = state["_slug"]
    agency = state["agency"]
    total_questions = f"{QUESTION_FLOOR}+"

    title = f"Free {name} Permit Practice Test (2026) | DriveDrill"
    description = meta_description(state)
    canonical = f"{SITE_URL}/practice/{slug}/"
    head = render_head(title, description, canonical,
                       alternates(f"/practice/{slug}/", f"/es/{slug}/"))

    question_blocks = "\n".join(
        render_question_block(q, i + 1) for i, q in enumerate(questions)
    )

    return f"""<!DOCTYPE html>
<html lang="en">
{head}
<body>
{render_header()}

<main class="wrap">
  <p class="crumbs"><a href="/practice/">&larr; All 51 practice tests</a><span class="sep" aria-hidden="true">&middot;</span><a href="/es/{slug}/" hreflang="es" lang="es">En español</a></p>
  <h1>Free {esc(name)} Permit Practice Test (2026)</h1>
  <p class="updated">10 sample questions below &middot; {total_questions} in the app &middot; answers explained</p>

  <div class="lede">{format_intro(state)}</div>

  <h2>Try {esc(name)}'s permit test</h2>
  <p>
    Pick an answer on each question to see whether you got it right, with an explanation.
    This is a short sample &mdash; DriveDrill has {total_questions} {esc(name)} questions
    and full-length mock exams in the app.
  </p>

  <p class="score-readout" id="score" aria-live="polite">0 of {len(questions)} answered</p>

  <div class="quiz" id="quiz">
{question_blocks}
  </div>

  <div class="endcard">
    <p>Practice all {total_questions} {esc(name)} questions and take full mock exams in the app.</p>
    <p class="cta">
      {store_button("car", "Get DriveDrill on the App Store")}
    </p>
  </div>

  <p>
    Official source: <a href="{esc(state['handbookURL'])}">{esc(name)} {esc(agency)} driver handbook</a>
    &mdash; always the authoritative reference for current rules.
  </p>

  <p class="disclaimer">
    DriveDrill is an independent study app. It is not affiliated with, endorsed by, or
    connected to any state motor vehicle agency. These practice questions are written in
    the style of {esc(name)}'s knowledge test and are not official test questions.
  </p>
</main>

{render_footer()}
{quiz_script(QUIZ_WORDS_EN)}
</body>
</html>
"""


def render_index_page(all_states: list[dict]) -> str:
    title = "Free DMV Permit Practice Tests — All 50 States + DC | DriveDrill"
    description = (
        "Free 10-question permit practice tests for all 50 states and DC. "
        "Instant answers, explanations, and a link to the app for the full question bank."
    )
    canonical = f"{SITE_URL}/practice/"

    items = []
    for state in all_states:
        name = state["stateName"]
        slug = state["_slug"]
        if state.get("examFormatVerified"):
            detail = f"{state['examQuestionCount']} questions &middot; pass with {state['passScore']}"
        else:
            detail = f"{QUESTION_FLOOR}+ practice questions"
        items.append(
            f'      <li><a href="/practice/{slug}/"><strong>{esc(name)}</strong>'
            f"<span>{detail}</span></a></li>"
        )
    items_html = "\n".join(items)

    return f"""<!DOCTYPE html>
<html lang="en">
{render_head(title, description, canonical)}
<body>
{render_header()}

<main class="wrap">
  <h1>Free practice tests for all 50 states + DC</h1>
  <p class="updated">Pick your state for a free 10-question sample of DriveDrill's permit practice test.</p>

  <div class="lede">
    Each page below has a short, free sample quiz with instant answers and explanations.
    Where a state's real knowledge-test format has been independently verified against its
    own DMV, the page states it; otherwise the page says so and points to the official
    handbook instead of guessing at numbers.
  </div>

  <p class="lang-switch" lang="es">¿Prefieres estudiar en español? <a href="/es/#estados" hreflang="es">Los 51 exámenes de práctica en español</a></p>

  <ul class="state-links">
{items_html}
  </ul>

  <p class="disclaimer">
    DriveDrill is an independent study app. It is not affiliated with, endorsed by, or
    connected to any state motor vehicle agency. Practice questions are written in the style
    of each state's knowledge test and are not official test questions.
  </p>
</main>

{render_footer()}
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
]
LEGAL_PAGES = ["/privacy.html", "/terms.html", "/support.html"]


def render_sitemap(all_states: list[dict]) -> str:
    urls = [(f"{SITE_URL}{path}", prio) for path, prio in STATIC_PAGES]
    for state in all_states:
        urls.append((f"{SITE_URL}/practice/{state['_slug']}/", "0.8"))
    for state in all_states:
        urls.append((f"{SITE_URL}/es/{state['_slug']}/", "0.8"))
    urls += [(f"{SITE_URL}{path}", "0.3") for path in LEGAL_PAGES]

    entries = "\n".join(
        f"  <url>\n    <loc>{esc(loc)}</loc>\n    <lastmod>{TODAY}</lastmod>\n"
        f"    <priority>{priority}</priority>\n  </url>"
        for loc, priority in urls
    )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
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
