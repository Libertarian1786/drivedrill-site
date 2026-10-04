#!/usr/bin/env python3
"""
Where every "Get DriveDrill" button on drivedrill.app points. ONE place, no JavaScript.

Every App Store button on the site is written the same way:

    <a class="button store-link" data-store="cdl" href="https://apps.apple.com/...">

data-store names one of the slots in STORE_LINKS below, and the href is always that slot's
address. To send a set of buttons somewhere else (an App Store custom product page, for
example), change that ONE line below, then run

    python tools/store_links.py

It rewrites the href of every store button in every page of the site, the hand-written pages
included, and prints how many it changed. tools/build_state_pages.py runs the same step at the
end of every build, and tools/check_site.py fails if any button disagrees with this file or if
any App Store link on the site does not go through it.
"""
from __future__ import annotations

import html
import re
import sys
from pathlib import Path

SITE_ROOT = Path(__file__).resolve().parent.parent

APP_STORE = "https://apps.apple.com/us/app/id6813140105"

STORE_LINKS = {
    "car": APP_STORE,            # home page buttons, /practice/<state>/, /guarantee/
    "cdl": APP_STORE,            # home page CDL section, /cdl/
    "motorcycle": APP_STORE,     # home page Motorcycle section, /motorcycle/
    "es-car": APP_STORE,         # home page "En español" section, /es/, /es/<state>/, /es/garantia/
    "es-cdl": APP_STORE,         # /es/cdl/ and the CDL section of /es/
    "es-motorcycle": APP_STORE,  # /es/motocicleta/ and the motorcycle section of /es/
}

A_TAG = re.compile(r"<a\b[^>]*>", re.IGNORECASE)
CLASS_ATTR = re.compile(r'(?<![\w-])class="([^"]*)"')
SLOT_ATTR = re.compile(r'(?<![\w-])data-store="([^"]*)"')
HREF_ATTR = re.compile(r'(?<![\w-])href="([^"]*)"')


def site_pages(root: Path = SITE_ROOT) -> list[Path]:
    """Every HTML page that is published (not .git, not tools)."""
    pages = []
    for path in sorted(root.rglob("*.html")):
        rel = path.relative_to(root).parts
        if rel[0] in (".git", "tools") or rel[0].startswith("."):
            continue
        pages.append(path)
    return pages


def is_store_link(tag: str) -> bool:
    cls = CLASS_ATTR.search(tag)
    return bool(cls) and "store-link" in cls.group(1).split()


def store_link_tags(text: str):
    """(slot, href, tag) for every store button in a page."""
    for m in A_TAG.finditer(text):
        tag = m.group(0)
        if not is_store_link(tag):
            continue
        slot = SLOT_ATTR.search(tag)
        href = HREF_ATTR.search(tag)
        yield (slot.group(1) if slot else None, html.unescape(href.group(1)) if href else None, tag)


def _rewrite(text: str, where: str) -> tuple[str, int]:
    changed = 0

    def fix(m: re.Match) -> str:
        nonlocal changed
        tag = m.group(0)
        if not is_store_link(tag):
            return tag
        slot = SLOT_ATTR.search(tag)
        if not slot:
            raise SystemExit(f"{where}: a store-link with no data-store: {tag}")
        if slot.group(1) not in STORE_LINKS:
            raise SystemExit(f"{where}: unknown data-store {slot.group(1)!r} (slots: {', '.join(STORE_LINKS)})")
        if not HREF_ATTR.search(tag):
            raise SystemExit(f"{where}: a store-link with no href: {tag}")
        want = html.escape(STORE_LINKS[slot.group(1)], quote=True)
        new = HREF_ATTR.sub(lambda _: f'href="{want}"', tag, count=1)
        if new != tag:
            changed += 1
        return new

    return A_TAG.sub(fix, text), changed


def apply_store_links(root: Path = SITE_ROOT, quiet: bool = False) -> int:
    """Point every store button at its slot's address. Returns the number of hrefs changed."""
    total = 0
    for path in site_pages(root):
        with path.open(encoding="utf-8", newline="") as f:
            text = f.read()
        new, changed = _rewrite(text, str(path.relative_to(root)))
        if changed:
            with path.open("w", encoding="utf-8", newline="") as f:
                f.write(new)
            total += changed
            if not quiet:
                print(f"  {changed:3d} store link(s) updated in {path.relative_to(root).as_posix()}")
    return total


def main() -> None:
    root = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else SITE_ROOT
    n = apply_store_links(root)
    buttons = sum(len(list(store_link_tags(p.read_text(encoding="utf-8")))) for p in site_pages(root))
    print(f"{n} store link(s) changed; {buttons} store buttons on the site in all.")


if __name__ == "__main__":
    main()
