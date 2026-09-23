#!/usr/bin/env python3
"""
Generates the free state practice-test pages for drivedrill.app.

Reads the 51 question packs (50 states + DC) from the DriveDrill app repo
(read-only) and writes:

  practice/<state-slug>/index.html   -- one 10-question sample quiz per state
  practice/index.html                -- listing page linking to all 51
  sitemap.xml                        -- every page on the site

Source of truth for questions is DriveDrill-DMV; this script only reads it.
Re-run any time the question packs change to regenerate the pages.

Usage:
    python tools/build_state_pages.py [path-to-Content-dir]
"""
from __future__ import annotations

import html
import json
import random
import sys
from datetime import date
from pathlib import Path

SITE_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONTENT_DIR = Path(
    r"C:\dev\DriveDrill-DMV\DriveDrill\Resources\Content"
)

APP_STORE_URL = "https://apps.apple.com/us/app/id6813140105"
SITE_URL = "https://drivedrill.app"
TODAY = date.today().isoformat()

NUM_QUIZ_QUESTIONS = 10
NUM_STATE_SPECIFIC = 4  # of the 10, how many must be state-specific (id "<code>-...")

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


def format_intro(state: dict) -> str:
    """Format-facts paragraph. Only states real numbers when examFormatVerified
    is true; otherwise says the app mirrors the published format without
    presenting a question count or pass score as official.
    """
    name = state["stateName"]
    notes = (state.get("notes") or "").strip()

    if state.get("examFormatVerified"):
        if notes:
            return esc(notes)
        qcount = state["examQuestionCount"]
        passc = state["passScore"]
        pct = round(100 * passc / qcount)
        return (
            f"{esc(name)}'s permit knowledge test is {qcount} questions, and you need "
            f"{passc} correct (about {pct}%) to pass."
        )

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


def render_question_block(q: dict, idx: int) -> str:
    choices_html = []
    for i, choice in enumerate(q["choices"]):
        letter = CHOICE_LETTERS[i] if i < len(CHOICE_LETTERS) else str(i + 1)
        choices_html.append(
            f'      <button type="button" class="choice" data-i="{i}">'
            f'<span class="choice-letter">{letter}</span> {esc(choice)}</button>'
        )
    choices_block = "\n".join(choices_html)

    return f"""  <fieldset class="quiz-q" data-correct="{int(q['answerIndex'])}">
    <legend><span class="qnum">Q{idx}.</span> {esc(q['prompt'])}</legend>
    <div class="choices">
{choices_block}
    </div>
    <p class="explain" hidden><strong class="verdict"></strong><span class="explain-text"> {esc(q['explanation'])}</span></p>
  </fieldset>"""


QUIZ_SCRIPT = """<script>
(function () {
  var quiz = document.getElementById('quiz');
  var scoreEl = document.getElementById('score');
  if (!quiz || !scoreEl) return;
  var questions = quiz.querySelectorAll('.quiz-q');
  var total = questions.length;
  var answered = 0, correct = 0;

  function updateScore() {
    if (answered < total) {
      scoreEl.textContent = answered + ' of ' + total + ' answered' +
        (answered > 0 ? ' \\u00b7 ' + correct + ' correct so far' : '');
    } else {
      scoreEl.textContent = 'You got ' + correct + ' of ' + total + ' correct.';
    }
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
    verdict.textContent = isRight ? 'Correct.' : 'Not quite.';
    explain.hidden = false;

    answered++;
    if (isRight) correct++;
    updateScore();
  });
})();
</script>"""


def render_state_page(state: dict, all_states: list[dict]) -> str:
    name = state["stateName"]
    slug = state["_slug"]
    agency = state["agency"]
    # A floor that stays true for every app version in people's hands (packs have had 205 to
    # 245 questions); an exact count would be wrong for anyone on an older or newer version.
    total_questions = "200+"
    questions = pick_quiz_questions(state)

    title = f"Free {name} Permit Practice Test (2026) | DriveDrill"
    description = meta_description(state)
    canonical = f"{SITE_URL}/practice/{slug}/"

    question_blocks = "\n".join(
        render_question_block(q, i + 1) for i, q in enumerate(questions)
    )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(title)}</title>
<meta name="description" content="{esc(description)}">
<link rel="canonical" href="{canonical}">
<link rel="icon" href="/favicon-32.png" sizes="32x32">
<link rel="apple-touch-icon" href="/apple-touch-icon.png">
<meta name="theme-color" content="#101216">
<meta property="og:type" content="website">
<meta property="og:site_name" content="DriveDrill">
<meta property="og:title" content="{esc(title)}">
<meta property="og:description" content="{esc(description)}">
<meta property="og:url" content="{canonical}">
<meta property="og:image" content="{SITE_URL}/og.png">
<meta property="og:image:width" content="1200">
<meta property="og:image:height" content="630">
<meta name="twitter:card" content="summary_large_image">
<meta name="twitter:title" content="{esc(title)}">
<meta name="twitter:description" content="{esc(description)}">
<meta name="twitter:image" content="{SITE_URL}/og.png">
<link rel="stylesheet" href="/style.css">
</head>
<body>
{render_header()}

<main class="wrap">
  <p class="crumbs"><a href="/practice/">&larr; All 51 practice tests</a></p>
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
      <a class="button" href="{APP_STORE_URL}">Get DriveDrill on the App Store</a>
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
{QUIZ_SCRIPT}
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
            detail = "200+ practice questions"
        items.append(
            f'      <li><a href="/practice/{slug}/"><strong>{esc(name)}</strong>'
            f"<span>{detail}</span></a></li>"
        )
    items_html = "\n".join(items)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(title)}</title>
<meta name="description" content="{esc(description)}">
<link rel="canonical" href="{canonical}">
<link rel="icon" href="/favicon-32.png" sizes="32x32">
<link rel="apple-touch-icon" href="/apple-touch-icon.png">
<meta name="theme-color" content="#101216">
<meta property="og:type" content="website">
<meta property="og:site_name" content="DriveDrill">
<meta property="og:title" content="{esc(title)}">
<meta property="og:description" content="{esc(description)}">
<meta property="og:url" content="{canonical}">
<meta property="og:image" content="{SITE_URL}/og.png">
<meta property="og:image:width" content="1200">
<meta property="og:image:height" content="630">
<meta name="twitter:card" content="summary_large_image">
<meta name="twitter:title" content="{esc(title)}">
<meta name="twitter:description" content="{esc(description)}">
<meta name="twitter:image" content="{SITE_URL}/og.png">
<link rel="stylesheet" href="/style.css">
</head>
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


def render_sitemap(all_states: list[dict]) -> str:
    urls = [
        (f"{SITE_URL}/", "1.0"),
        (f"{SITE_URL}/practice/", "0.9"),
    ]
    for state in all_states:
        urls.append((f"{SITE_URL}/practice/{state['_slug']}/", "0.8"))
    urls += [
        (f"{SITE_URL}/privacy.html", "0.3"),
        (f"{SITE_URL}/terms.html", "0.3"),
        (f"{SITE_URL}/support.html", "0.3"),
    ]

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


def main() -> None:
    content_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_CONTENT_DIR
    if not content_dir.is_dir():
        print(f"Content dir not found: {content_dir}", file=sys.stderr)
        sys.exit(1)

    all_states = load_states(content_dir)
    if len(all_states) != 51:
        print(f"WARNING: expected 51 state packs, found {len(all_states)}", file=sys.stderr)

    practice_dir = SITE_ROOT / "practice"
    practice_dir.mkdir(exist_ok=True)

    verified_count = 0
    for state in all_states:
        page_dir = practice_dir / state["_slug"]
        page_dir.mkdir(parents=True, exist_ok=True)
        html_out = render_state_page(state, all_states)
        (page_dir / "index.html").write_text(html_out, encoding="utf-8")
        if state.get("examFormatVerified"):
            verified_count += 1

    (practice_dir / "index.html").write_text(render_index_page(all_states), encoding="utf-8")
    (SITE_ROOT / "sitemap.xml").write_text(render_sitemap(all_states), encoding="utf-8")

    print(f"Generated {len(all_states)} state pages under {practice_dir}")
    print(f"  examFormatVerified numbers shown: {verified_count} states")
    print(f"  unverified (generic copy, no numbers): {len(all_states) - verified_count} states")
    print(f"Wrote {practice_dir / 'index.html'}")
    print(f"Wrote {SITE_ROOT / 'sitemap.xml'}")


if __name__ == "__main__":
    main()
