# drivedrill.app

Public marketing and legal pages for **DriveDrill** (DMV permit practice test, iOS).

Served by GitHub Pages at <https://drivedrill.app>. This repo is the source of truth for the site. The app repo's `site/` folder (privacy and
terms copies) must be kept in step with privacy.html and terms.html here, so the app and the
site never disagree about the terms they both reference.

- `/` landing
- `/privacy.html` — linked from the app and from App Store Connect
- `/terms.html` — linked from the paywall
- `/support.html` — the support URL on the app record
- `/cdl/`, `/motorcycle/` — CDL and motorcycle prep (hand-written)
- `/es/`, `/es/cdl/`, `/es/motocicleta/` — the Spanish twins (hand-written; the state list on
  `/es/` is generated)
- `/guarantee/`, `/es/garantia/` — the Pass Guarantee and its Spanish twin (hand-written). The
  same terms are a section of terms.html; support.html says what to send for a claim; each
  selling page carries one line about it under its first App Store button
- `/practice/<state>/` and `/es/<state>/` — 51 English and 51 Spanish sample-quiz pages
  (generated)

No analytics, no trackers, no external requests. That is deliberate: the privacy policy
says the app collects nothing, and this site has to be able to say the same.

## Generated pages

    python tools/build_state_pages.py

reads the question packs from the app repo at the commit in `APP_REF` (the version live on
the App Store; bump it when a version with new questions ships), rewrites `practice/`,
`es/<state>/`, the state list on `es/index.html` and `sitemap.xml`, then runs
`tools/check_site.py`, which fails on any parse error, broken internal link, missing title,
description or canonical, hreflang pair without its return link, stray App Store link, price,
currency word or trial length in the copy, pass-rate claim, page that cannot be reached from
the home page, selling page without its Pass Guarantee line, or sitemap gap. Run the checker on
its own after editing any page.

## App Store links: one place

Every App Store button is `<a class="button store-link" data-store="SLOT" href="...">`, and
every slot's address lives in `tools/store_links.py`. To point a set of buttons at a custom
product page, change that slot's ONE line there, run `python tools/store_links.py` (it
rewrites every page), check, commit, push.
