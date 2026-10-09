#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
The Spanish state pages of drivedrill.app: es/<state-slug>/index.html, one per state, each
the twin of practice/<state-slug>/ (same structure, same 10 questions, from the app's own
Spanish packs), and the state list on es/index.html between its BEGIN/END markers.

Run tools/build_state_pages.py, which builds the English and Spanish pages together so the
twins, the hreflang pairs and the sitemap always agree. Running this file does the same.

Wording follows the app's Spanish (tools/i18n/app_es.json and GLOSSARY.md in the app repo):
examen de conocimientos = knowledge test, simulacro = mock exam, prueba de manejo = road test,
Ajustes = the app's settings, Configuración = the iPhone's Settings app. The app is never
called free ("gratis"): only the trial is.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path

import build_state_pages as en
from build_state_pages import CHECK_SVG, QUESTION_FLOOR, SITE_URL, esc

# The Spanish store wording (owner's decision 2026-10-05), not a translation of the English.
PILL_ES = "EDICIÓN 2026"
TRIAL_ES = "3 días de prueba gratis en los planes semanales"
GUARANTEE_NOTE_ES = ('Garantía de aprobación: apruebas tu examen de conocimientos o te devolvemos tu dinero. '
                     '<a href="/es/garantia/">Condiciones.</a>')
FOOTER_LINE_ES = "DriveDrill es una app de estudio independiente y no está afiliada a ningún organismo estatal."

# The hero phone: the Spanish capture of the same screens the English pages use.
PHONE_ES = ("/img/phone-practice-es.jpg",
            "Una pregunta de práctica de DriveDrill en español sobre una ambulancia que se acerca a "
            "una intersección, con una ilustración de la escena.")
PHONE_ES_BY_STATE = {
    "CA": ("/img/phone-exam-ca-es.jpg",
           "El simulacro de examen de California en DriveDrill, en español: pregunta 16 de 46."),
}

# Spanish for the 9 verified states whose pack has notes, keyed by state code, with the English
# they translate. If a pack's English notes change, the build stops until this is updated.
ES_NOTES = {
    "CA": (
        "Applicants under 18 take 46 questions and must answer 38 correctly. Adults 18 and over "
        "are given a shorter version (36 questions, 30 correct). This app practices the "
        "46-question format.",
        "Los solicitantes menores de 18 años responden 46 preguntas y deben contestar 38 "
        "correctamente. A los adultos de 18 años o más se les da una versión más corta (36 "
        "preguntas, 30 correctas). Esta app practica el formato de 46 preguntas.",
    ),
    "FL": (
        "The Florida Class E knowledge exam is 50 questions covering road rules and road signs; "
        "40 correct (80%) passes.",
        "El examen de conocimientos de Clase E de Florida tiene 50 preguntas sobre reglas de "
        "tránsito y señales de tránsito; con 40 correctas (80%) apruebas.",
    ),
    "GA": (
        "Georgia's knowledge exam has two independently scored sections of 20 questions. You must "
        "score at least 15 of 20 in EACH.",
        "El examen de conocimientos de Georgia tiene dos secciones de 20 preguntas que se "
        "califican por separado. Debes obtener al menos 15 de 20 en CADA una.",
    ),
    "MD": (
        "Maryland has one of the strictest thresholds in the country at 88%.",
        "Maryland tiene uno de los puntajes mínimos más exigentes del país: el 88%.",
    ),
    "NY": (
        "New York's written test is 20 questions; you need 14 correct AND at least 2 of the 4 "
        "road sign questions.",
        "El examen escrito de Nueva York tiene 20 preguntas; necesitas 14 correctas Y al menos "
        "2 de las 4 preguntas sobre señales de tránsito.",
    ),
    "OH": (
        "Ohio's knowledge test is 40 questions and you must answer 75% of them (30) correctly. It "
        "is scored as one test, not as separate sections.",
        "El examen de conocimientos de Ohio tiene 40 preguntas y debes contestar correctamente "
        "el 75% (30). Se califica como un solo examen, no por secciones separadas.",
    ),
    "OR": (
        "Applicants who do not complete an approved driver education course must log 100 "
        "supervised hours instead of 50.",
        "Los solicitantes que no completen un curso aprobado de educación vial deben registrar "
        "100 horas de conducción supervisada en lugar de 50.",
    ),
    "PA": (
        "PennDOT's knowledge test is 18 questions; 15 correct passes.",
        "El examen de conocimientos de PennDOT tiene 18 preguntas; con 15 correctas apruebas.",
    ),
    "VA": (
        "Virginia's exam has two parts. Part one is 10 road sign questions and you must answer ALL "
        "TEN correctly before part two is offered. Part two is 30 general knowledge questions and "
        "needs 24 correct.",
        "El examen de Virginia tiene dos partes. La primera parte son 10 preguntas de señales de "
        "tránsito, y debes contestar correctamente LAS DIEZ antes de que se te ofrezca la segunda "
        "parte. La segunda parte son 30 preguntas de conocimientos generales y exige 24 correctas.",
    ),
}

# A state whose Spanish name takes the article inside a sentence (the app's pack strings).
ARTICLE = {"DC": "el"}

MONTHS = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto",
          "septiembre", "octubre", "noviembre", "diciembre"]

LIST_BEGIN = "<!-- BEGIN es-state-list: generated by tools/build_state_pages.py; edit the generator, not this list -->"
LIST_END = "<!-- END es-state-list -->"


@dataclass
class Names:
    bare: str      # "Distrito de Columbia" (labels, lists)
    sentence: str  # "el Distrito de Columbia" (inside a sentence)
    de: str        # "del Distrito de Columbia" / "de California"
    subj: str      # "El Distrito de Columbia" (opening a sentence)


def contract(text: str) -> str:
    """The app's AppLanguage.sentence: 'de el' -> 'del', 'a el' -> 'al'."""
    return re.sub(r"\bde el\b", "del", re.sub(r"\ba el\b", "al", text))


def names_for(code: str, es_pack: dict) -> Names:
    bare = es_pack["stateName"]
    sentence = f"{ARTICLE[code]} {bare}" if code in ARTICLE else bare
    return Names(bare=bare, sentence=sentence, de=contract(f"de {sentence}"),
                 subj=sentence[0].upper() + sentence[1:])


def long_date(iso: str) -> str:
    y, m, d = (int(x) for x in iso.split("-"))
    return f"{d} de {MONTHS[m - 1]} de {y}"


def spanish_note(state: dict) -> str:
    code, notes = state["stateCode"], (state.get("notes") or "").strip()
    if code not in ES_NOTES:
        raise SystemExit(f"{code}: the English notes have no Spanish in ES_NOTES (tools/build_es_pages.py): {notes!r}")
    english, spanish = ES_NOTES[code]
    if english != notes:
        raise SystemExit(f"{code}: the English notes changed; update ES_NOTES in tools/build_es_pages.py.\n"
                         f"  now: {notes!r}\n  was: {english!r}")
    return spanish


def format_intro(state: dict, n: Names) -> str:
    """The Spanish of build_state_pages.format_intro: numbers only for a verified format."""
    notes = (state.get("notes") or "").strip()
    if state.get("examFormatVerified"):
        qcount, passc = state["examQuestionCount"], state["passScore"]
        pct = round(100 * passc / qcount)
        computed = (
            f"El examen de conocimientos para el permiso {esc(n.de)} tiene {qcount} preguntas, "
            f"y necesitas {passc} correctas (alrededor del {pct}%) para aprobar."
        )
        if not notes:
            return computed
        note = esc(spanish_note(state))
        if en.notes_state_the_format(notes, qcount):
            return note
        return f"{computed} {note}"
    return (
        f"{esc(n.subj)} no publica en una página oficial que pudiéramos confirmar cuántas preguntas "
        f"tiene su examen de conocimientos ni cuántas hay que contestar bien para aprobar, así que "
        f"aquí no damos cifras. Las preguntas de este cuestionario están escritas al estilo del "
        f"examen de conocimientos {esc(n.de)}, y el manual oficial del conductor {esc(n.de)} "
        f"(enlace abajo) es la fuente para estudiar."
    )


def spanish_test_notice(state: dict, n: Names) -> str:
    """The app's SpanishTestNotice: shown only where the knowledge test is NOT given in Spanish
    ("no") or no official source says it is ("unconfirmed"); nothing otherwise."""
    test = state.get("spanishTest") or {}
    availability = test.get("knowledgeTestInSpanish")
    if availability == "no":
        since = test.get("englishOnlySince")
        text = (f"En {n.sentence}, el examen de conocimientos se da solo en inglés desde el {long_date(since)}."
                if since else f"En {n.sentence}, el examen de conocimientos se da solo en inglés.")
        link = test.get("sourceURL")
        source = f' <a href="{esc(link)}">Fuente oficial</a>' if link else ""
        return f'      <div class="lede notice">{esc(contract(text))}{source}</div>\n\n'
    if availability == "unconfirmed":
        text = (f"No encontramos ninguna confirmación oficial de que {n.sentence} ofrezca el "
                f"examen de conocimientos en español.")
        return f'      <div class="lede notice">{esc(contract(text))}</div>\n\n'
    return ""


def facts_es(state: dict) -> tuple[str, str]:
    """The Spanish of build_state_pages.facts_en, in the app's words (simulacro, apruebas con,
    examen de práctica): a format only where the state's format is confirmed."""
    count = f"{len(state['questions'])} preguntas"
    if state.get("examFormatVerified"):
        return count, f"simulacro de {state['examQuestionCount']}, apruebas con {state['passScore']}"
    return count, "examen de práctica completo"


def meta_description(state: dict, n: Names) -> str:
    count = len(state["questions"])
    if state.get("examFormatVerified"):
        exam = (f"{count} preguntas, simulacro de {state['examQuestionCount']}, "
                f"apruebas con {state['passScore']}.")
    else:
        exam = f"{count} preguntas y un examen de práctica completo."
    return f"Aprueba el examen {n.de} a la primera. {exam} Cada respuesta, explicada. Pruébalo aquí."


def render_header(en_href: str = "/") -> str:
    """The blue header of every Spanish page; en_href is the page's English twin."""
    return f"""<header class="top">
  <div class="wrap top-in">
    <a class="brand" href="/es/"><img src="/img/icon-192.png" alt="" width="32" height="32">DriveDrill</a>
    <nav class="nav" aria-label="Principal">
      <a class="d-only" href="/es/#estados">Estados</a>
      <a href="/es/cdl/">CDL</a>
      <a href="/es/motocicleta/">Motocicleta</a>
      <a class="d-only" href="/es/garantia/">Garantía</a>
      <a class="lang" href="{en_href}" hreflang="en" lang="en" aria-label="English"><span class="d-only">English</span><span class="m-only">EN</span></a>
    </nav>
  </div>
</header>"""


def render_footer(en_href: str = "/") -> str:
    return f"""<footer class="foot">
  <div class="wrap">
    <div class="foot-top">
      <a class="brand" href="/es/"><img src="/img/icon-192.png" alt="" width="32" height="32">DriveDrill</a>
      <nav class="foot-nav" aria-label="Pie de página">
        <a href="/es/#estados">Exámenes de práctica</a>
        <a href="/es/garantia/">Garantía de aprobación</a>
        <a href="/privacy.html" hreflang="en">Privacidad</a>
        <a href="/terms.html" hreflang="en">Términos</a>
        <a href="/support.html" hreflang="en">Soporte</a>
        <a href="{en_href}" hreflang="en" lang="en">English</a>
      </nav>
    </div>
    <p class="disclaimer-line">{FOOTER_LINE_ES}</p>
    <p class="copy">&copy; 2026 DriveDrill</p>
  </div>
</footer>"""


QUIZ_WORDS_ES = """  var T = {
    progress: function (a, c, t) {
      return a + ' de ' + t + ' respondidas' + (a > 0 ? ' \\u00b7 ' + c + (c === 1 ? ' correcta' : ' correctas') + ' hasta ahora' : '');
    },
    done: function (c, t) { return 'Acertaste ' + c + ' de ' + t + '.'; },
    right: 'Correcto.',
    wrong: 'Esta vez no.'
  };"""


def render_state_page(state: dict, es_pack: dict, questions: list[dict]) -> str:
    code, slug, agency = state["stateCode"], state["_slug"], state["agency"]
    n = names_for(code, es_pack)
    floor = QUESTION_FLOOR

    title = f"Examen de manejo {n.de} 2026: preguntas de práctica | DriveDrill"
    head = en.render_head(title, meta_description(state, n), f"{SITE_URL}/es/{slug}/",
                          en.alternates(f"/practice/{slug}/", f"/es/{slug}/"))
    blocks = "\n".join(en.render_question_block(q, i + 1, qprefix="P") for i, q in enumerate(questions))

    hero = en.render_hero(
        crumbs=(f'<p class="crumbs"><a href="/es/#estados">&larr; Los 51 exámenes de práctica</a>'
                f'<a href="/practice/{slug}/" hreflang="en" lang="en">In English</a></p>'),
        pill=PILL_ES,
        h1=f'Aprueba el examen {esc(n.de)} <span class="hl">a la primera.</span>',
        facts=en.facts_line(*facts_es(state)),
        cta=(f'{en.store_button("es-car", "es")}\n'
             f'          <p class="trial-note">{CHECK_SVG}{TRIAL_ES}</p>\n'
             f'          <p class="g-note">{GUARANTEE_NOTE_ES}</p>'),
        phone=PHONE_ES_BY_STATE.get(code, PHONE_ES),
    )

    return f"""<!DOCTYPE html>
<html lang="es">
{head}
<body>
{render_header(f"/practice/{slug}/")}

<main>
{hero}

  <section class="sec" id="muestra" aria-labelledby="sample-title">
    <div class="wrap quiz-wrap">
      <p class="updated">10 preguntas de muestra abajo &middot; más de {floor} en la app &middot; respuestas explicadas</p>

{spanish_test_notice(state, n)}      <div class="lede">{format_intro(state, n)}</div>

      <h2 class="sec-title" id="sample-title">Practica con preguntas {esc(n.de)}</h2>
      <p class="sec-sub">
        Elige una respuesta en cada pregunta para ver si acertaste, con una explicación.
        Es una muestra corta: DriveDrill tiene más de {floor} preguntas {esc(n.de)}
        y simulacros de examen completos en la app.
      </p>

      <p class="score-readout" id="score" aria-live="polite">0 de {len(questions)} respondidas</p>

      <div class="quiz" id="quiz">
{blocks}
      </div>

      <div class="endcard">
        <p>Practica las más de {floor} preguntas {esc(n.de)} y toma simulacros de examen completos en la app, en español.</p>
        <p class="cta">{en.store_button("es-car", "es")}</p>
        <p class="cta-note">Si tu iPhone está en español, DriveDrill se abre en español. Si no, en la app toca Ajustes › Idioma: se abre DriveDrill en la app Configuración, donde puedes elegir Español.</p>
      </div>

      <div class="after-quiz">
        <p>
          Fuente oficial: <a href="{esc(state['handbookURL'])}">manual del conductor {esc(n.de)} ({esc(agency)})</a>
          &mdash; siempre la referencia autorizada para las reglas vigentes.
        </p>
        <p class="disclaimer">
          DriveDrill es una app de estudio independiente. No está afiliada a, respaldada por, ni
          conectada con ningún organismo estatal de vehículos motorizados. Estas preguntas de práctica
          están escritas al estilo del examen de conocimientos {esc(n.de)} y no son preguntas
          oficiales del examen.
        </p>
      </div>
    </div>
  </section>
</main>

{render_footer(f"/practice/{slug}/")}
{en.quiz_script(QUIZ_WORDS_ES)}
</body>
</html>
"""


def sort_key(name: str) -> str:
    return unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower()


def render_state_list(states: list[dict], es_packs: dict[str, dict]) -> str:
    """The /es/ state list: one chip per state, in Spanish alphabetical order. The "Busca tu
    estado" box above it filters the chips (es/index.html carries build_state_pages.FILTER_SCRIPT)."""
    rows = []
    for state in sorted(states, key=lambda s: sort_key(es_packs[s["stateCode"]]["stateName"])):
        name = es_packs[state["stateCode"]]["stateName"]
        rows.append(f'        <li><a href="/es/{state["_slug"]}/">{esc(name)}</a></li>')
    return '      <ul class="state-list" id="state-list">\n' + "\n".join(rows) + "\n      </ul>"


def inject_state_list(index_path: Path, list_html: str) -> None:
    with index_path.open(encoding="utf-8", newline="") as f:
        text = f.read()
    start, end = text.find(LIST_BEGIN), text.find(LIST_END)
    if start < 0 or end < start:
        raise SystemExit(f"{index_path}: the BEGIN/END es-state-list markers are missing")
    nl = "\r\n" if "\r\n" in text else "\n"
    line_start = text.rfind("\n", 0, end) + 1
    indent = text[line_start:end] if not text[line_start:end].strip() else ""  # the END marker's own
    new = text[: start + len(LIST_BEGIN)] + nl + list_html.replace("\n", nl) + nl + indent + text[end:]
    if new != text:
        with index_path.open("w", encoding="utf-8", newline="") as f:
            f.write(new)


def build(site_root: Path, states: list[dict], es_packs: dict[str, dict]) -> int:
    """Writes es/<slug>/index.html for every state and the /es/ list. Needs each state's
    '_quiz_ids' (set by build_state_pages) so each twin shows the same questions."""
    for state in states:
        if state.get("examFormatVerified") and (state.get("notes") or "").strip():
            spanish_note(state)  # fail early on a missing or stale translation
    es_dir = site_root / "es"
    for state in states:
        pack = es_packs[state["stateCode"]]
        by_id = {q["id"]: q for q in pack["questions"]}
        questions = [by_id[qid] for qid in state["_quiz_ids"]]
        page_dir = es_dir / state["_slug"]
        page_dir.mkdir(parents=True, exist_ok=True)
        (page_dir / "index.html").write_text(render_state_page(state, pack, questions), encoding="utf-8")
    inject_state_list(es_dir / "index.html", render_state_list(states, es_packs))
    return len(states)


if __name__ == "__main__":
    en.main()
