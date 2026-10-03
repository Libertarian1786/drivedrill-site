#!/usr/bin/env python3
"""
Checks every published page of drivedrill.app before it is pushed. Standard library only.

  * every HTML page parses: doctype, balanced and properly nested tags, unique ids, one <title>
  * no broken internal link: every href/src on the site (relative, /root, or
    https://drivedrill.app/...) resolves to a file in this repo, and every #fragment to an id
  * head tags: title and meta description everywhere; a canonical that matches the page's
    own URL; hreflang en/es/x-default pairs that point at each other, on every page that has
    a twin in the other language
  * store links: every App Store link goes through the one pattern in tools/store_links.py,
    and every page that sells the app has at least one
  * the site stays analytics-free: no external script, stylesheet, iframe or image
  * copy guards: no price, no trial length, and no "gratis" in a Spanish title or description
  * sitemap.xml lists exactly the pages that have a canonical, and robots.txt points at it

Usage:
    python tools/check_site.py            # exits 1 and lists every problem if anything fails
"""
from __future__ import annotations

import re
import sys
import xml.etree.ElementTree as ET
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urldefrag, urlparse

TOOLS = Path(__file__).resolve().parent
SITE_ROOT = TOOLS.parent
sys.path.insert(0, str(TOOLS))

import store_links  # noqa: E402

SITE = "https://drivedrill.app"
VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param",
        "source", "track", "wbr"}
SKIP_FILES = {"googled6a639a37ec9b0af.html"}       # Search Console token, not a page
LEGACY = {"/privacy.html", "/terms.html", "/support.html"}  # older pages: no canonical, no twin
PRICE_FREE = {"/", "/practice/", "/cdl/", "/motorcycle/", "/es/", "/es/cdl/", "/es/motocicleta/"}
STATE_SLUGS_MIN = 51


class Page(HTMLParser):
    def __init__(self, url_path: str):
        super().__init__(convert_charrefs=True)
        self.url_path = url_path
        self.errors: list[str] = []
        self.stack: list[tuple[str, int]] = []
        self.ids: dict[str, int] = {}
        self.links: list[tuple[str, str, str, int]] = []   # (tag, attr, value, line)
        self.a_tags: list[dict] = []
        self.titles: list[str] = []
        self.meta: dict[str, str] = {}
        self.canonical: list[str] = []
        self.hreflang: dict[str, str] = {}
        self.html_lang: str | None = None
        self.h1 = 0
        self.scripts_external: list[str] = []
        self.iframes = 0
        self._in_title = False
        self._text: list[str] = []
        self.saw_doctype = False

    # --- structure
    def handle_decl(self, decl):
        if decl.lower().strip() == "doctype html":
            self.saw_doctype = True

    def handle_starttag(self, tag, attrs):
        self._start(tag, attrs, selfclosing=False)

    def handle_startendtag(self, tag, attrs):
        self._start(tag, attrs, selfclosing=True)

    def _start(self, tag, attrs, selfclosing):
        line = self.getpos()[0]
        a = {k: (v if v is not None else "") for k, v in attrs}
        if tag not in VOID and not selfclosing:
            self.stack.append((tag, line))
        if "id" in a:
            if a["id"] in self.ids:
                self.errors.append(f"line {line}: duplicate id {a['id']!r} (first on line {self.ids[a['id']]})")
            self.ids.setdefault(a["id"], line)
        if tag == "html":
            self.html_lang = a.get("lang")
        elif tag == "title":
            self._in_title = True
            self.titles.append("")
        elif tag == "h1":
            self.h1 += 1
        elif tag == "meta" and "name" in a:
            self.meta[a["name"]] = a.get("content", "")
        elif tag == "link" and a.get("rel") == "canonical":
            self.canonical.append(a.get("href", ""))
        elif tag == "link" and a.get("rel") == "alternate" and "hreflang" in a:
            if a["hreflang"] in self.hreflang:
                self.errors.append(f"line {line}: hreflang {a['hreflang']!r} declared twice")
            self.hreflang[a["hreflang"]] = a.get("href", "")
        elif tag == "script" and a.get("src"):
            self.scripts_external.append(a["src"])
        elif tag == "iframe":
            self.iframes += 1
        if tag == "a":
            self.a_tags.append({**a, "_line": line})
        for attr in ("href", "src"):
            if attr in a:
                self.links.append((tag, attr, a[attr], line))

    def handle_endtag(self, tag):
        line = self.getpos()[0]
        if tag in VOID:
            self.errors.append(f"line {line}: end tag </{tag}> for a void element")
            return
        if tag == "title":
            self._in_title = False
        if not self.stack:
            self.errors.append(f"line {line}: stray </{tag}>")
            return
        top, opened = self.stack[-1]
        if top != tag:
            self.errors.append(f"line {line}: </{tag}> closes <{top}> opened on line {opened}")
            # recover: pop up to the matching tag if it is open
            names = [t for t, _ in self.stack]
            if tag in names:
                while self.stack and self.stack[-1][0] != tag:
                    self.stack.pop()
                self.stack.pop()
            return
        self.stack.pop()

    def handle_data(self, data):
        if self._in_title and self.titles:
            self.titles[-1] += data
        self._text.append(data)

    def finish(self):
        self.close()
        for tag, line in self.stack:
            self.errors.append(f"line {line}: <{tag}> is never closed")
        if not self.saw_doctype:
            self.errors.append("missing <!DOCTYPE html>")

    @property
    def text(self) -> str:
        return " ".join(self._text)


def url_path_of(file: Path) -> str:
    rel = file.relative_to(SITE_ROOT).as_posix()
    if rel == "index.html":
        return "/"
    if rel.endswith("/index.html"):
        return "/" + rel[: -len("index.html")]
    return "/" + rel


def resolve(page_path: str, target: str) -> tuple[str, str] | None:
    """(url path, fragment) for an internal link, or None for an external one."""
    if target.startswith(("mailto:", "tel:", "data:", "javascript:")):
        return None
    parsed = urlparse(target)
    if parsed.scheme in ("http", "https"):
        if parsed.netloc not in ("drivedrill.app", "www.drivedrill.app"):
            return None
        path = parsed.path or "/"
    elif parsed.scheme:
        return None
    else:
        base = page_path if page_path.endswith("/") else page_path.rsplit("/", 1)[0] + "/"
        path = parsed.path
        if not path:
            path = page_path
        elif not path.startswith("/"):
            parts = (base + path).split("/")
            out: list[str] = []
            for p in parts:
                if p == "..":
                    if out:
                        out.pop()
                elif p != ".":
                    out.append(p)
            path = "/".join(out)
            if not path.startswith("/"):
                path = "/" + path
    return unquote(path), parsed.fragment


def file_for(url_path: str) -> Path | None:
    p = SITE_ROOT / url_path.lstrip("/")
    if url_path.endswith("/"):
        p = p / "index.html"
    elif p.is_dir():
        return None  # "/es" without the slash would redirect; insist on the canonical form
    return p if p.is_file() else None


def main(argv: list[str] | None = None) -> int:
    errors: list[str] = []
    pages: dict[str, Page] = {}
    files = [f for f in store_links.site_pages(SITE_ROOT) if f.name not in SKIP_FILES]

    for f in files:
        path = url_path_of(f)
        p = Page(path)
        p.feed(f.read_text(encoding="utf-8"))
        p.finish()
        pages[path] = p
        errors += [f"{path}: {e}" for e in p.errors]

    slots = store_links.STORE_LINKS
    sellers = 0
    for path, p in pages.items():
        new_or_twin = path not in LEGACY
        # --- head
        if len(p.titles) != 1 or not p.titles[0].strip():
            errors.append(f"{path}: needs exactly one non-empty <title>")
        if not p.meta.get("description", "").strip():
            errors.append(f"{path}: no meta description")
        if p.h1 != 1:
            errors.append(f"{path}: {p.h1} <h1> elements (want 1)")
        if not p.html_lang:
            errors.append(f"{path}: <html> has no lang")
        if new_or_twin:
            if len(p.canonical) != 1:
                errors.append(f"{path}: needs exactly one canonical link")
            elif p.canonical[0] != SITE + path:
                errors.append(f"{path}: canonical {p.canonical[0]} is not {SITE + path}")
        # --- hreflang pairs
        if p.hreflang:
            if set(p.hreflang) != {"en", "es", "x-default"}:
                errors.append(f"{path}: hreflang set {sorted(p.hreflang)} (want en, es, x-default)")
            own = p.hreflang.get(p.html_lang or "")
            if own != SITE + path:
                errors.append(f"{path}: hreflang {p.html_lang!r} should be the page itself, is {own}")
            if p.hreflang.get("x-default") != p.hreflang.get("en"):
                errors.append(f"{path}: x-default should be the English page")
            for lang, url in p.hreflang.items():
                r = resolve(path, url)
                if not r or r[0] not in pages:
                    errors.append(f"{path}: hreflang {lang} -> {url} is not a page on this site")
                    continue
                twin = pages[r[0]]
                if twin.hreflang != p.hreflang:
                    errors.append(f"{path}: hreflang does not match its twin {r[0]} (no return link)")
                if lang != "x-default" and twin.html_lang != lang:
                    errors.append(f"{path}: hreflang {lang} points at a page whose lang is {twin.html_lang!r}")
        # --- store links
        store_tags = [a for a in p.a_tags if "store-link" in a.get("class", "").split()]
        for a in p.a_tags:
            href = a.get("href", "")
            if "apps.apple.com" in href and a not in store_tags:
                errors.append(f"{path}: line {a['_line']}: App Store link not using class=store-link: {href}")
        for a in store_tags:
            slot = a.get("data-store")
            if slot not in slots:
                errors.append(f"{path}: line {a['_line']}: store-link with unknown data-store {slot!r}")
            elif a.get("href") != slots[slot]:
                errors.append(f"{path}: line {a['_line']}: store-link {slot} href {a.get('href')} != tools/store_links.py")
        if store_tags:
            sellers += 1
        elif new_or_twin and path != "/practice/":
            errors.append(f"{path}: no App Store button (class=store-link)")
        # --- analytics-free
        if p.scripts_external:
            errors.append(f"{path}: external script(s) {p.scripts_external}")
        if p.iframes:
            errors.append(f"{path}: {p.iframes} iframe(s)")
        for tag, attr, value, line in p.links:
            if tag in ("img", "source", "link") and resolve(path, value) is None and not (
                tag == "link" and value.startswith(SITE)
            ):
                errors.append(f"{path}: line {line}: external {tag} {attr}={value}")
        # --- internal links
        for tag, attr, value, line in p.links:
            r = resolve(path, value)
            if r is None:
                continue
            target_path, frag = r
            target_file = file_for(target_path)
            if target_file is None:
                errors.append(f"{path}: line {line}: broken link {attr}={value} ({target_path})")
                continue
            if frag:
                target = pages.get(url_path_of(target_file))
                if target is None or frag not in target.ids:
                    errors.append(f"{path}: line {line}: {value} has no #{frag} on the target page")
        # --- copy guards (the older legal pages state prices on purpose; state pages are left
        # out of the price check because quiz questions quote fines such as "$250")
        if new_or_twin:
            text = p.text
            if path in PRICE_FREE and re.search(r"\$\s?\d", text):
                errors.append(f"{path}: a price in the copy ({re.search(r'.{0,30}\$\s?\d[^ ]*', text).group(0)!r})")
            m = re.search(r"\b\d+[- ]day free trial\b|\bprueba gratis de \d+\b|\b\d+ d[ií]as gratis\b", text, re.I)
            if m:
                errors.append(f"{path}: a trial length in the copy ({m.group(0)!r}); it is changing")
            if p.html_lang == "es":
                head_text = (p.titles[0] if p.titles else "") + " " + p.meta.get("description", "")
                if re.search(r"\bgratis\b", head_text, re.I):
                    errors.append(f"{path}: 'gratis' in the title or description")

    # --- sitemap and robots
    sm = SITE_ROOT / "sitemap.xml"
    try:
        ns = {"s": "http://www.sitemaps.org/schemas/sitemap/0.9"}
        locs = [e.text.strip() for e in ET.parse(sm).getroot().findall("s:url/s:loc", ns)]
    except ET.ParseError as exc:
        errors.append(f"sitemap.xml does not parse: {exc}")
        locs = []
    if len(locs) != len(set(locs)):
        errors.append("sitemap.xml lists a URL twice")
    for loc in locs:
        r = resolve("/", loc)
        if not r or r[0] not in pages:
            errors.append(f"sitemap.xml: {loc} is not a page on this site")
    want = {SITE + path for path, p in pages.items() if p.canonical} | {SITE + x for x in LEGACY}
    for missing in sorted(want - set(locs)):
        errors.append(f"sitemap.xml: missing {missing}")
    robots = (SITE_ROOT / "robots.txt").read_text(encoding="utf-8")
    if f"Sitemap: {SITE}/sitemap.xml" not in robots:
        errors.append("robots.txt does not point at the sitemap")
    if re.search(r"(?im)^\s*Disallow:\s*/\s*$", robots):
        errors.append("robots.txt disallows the whole site")

    es_states = [x for x in pages if re.fullmatch(r"/es/[a-z-]+/", x) and x not in ("/es/cdl/", "/es/motocicleta/")]
    en_states = [x for x in pages if re.fullmatch(r"/practice/[a-z-]+/", x)]
    if len(es_states) < STATE_SLUGS_MIN or len(en_states) < STATE_SLUGS_MIN:
        errors.append(f"expected 51 state pages in each language, found {len(en_states)} English, {len(es_states)} Spanish")

    links = sum(len(p.links) for p in pages.values())
    print(f"check_site: {len(pages)} pages parsed, {links} links checked, {len(locs)} sitemap URLs, "
          f"{len(en_states)} English + {len(es_states)} Spanish state pages, {sellers} pages with a store button")
    if errors:
        print(f"check_site: {len(errors)} problem(s):")
        for e in errors:
            print("  - " + e)
        return 1
    print("check_site: OK")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
