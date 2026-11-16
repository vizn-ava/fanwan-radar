# Third-party source

`boss-zhipin-scraper` is an unmodified checkout of
https://github.com/eatmoreduck/boss-zhipin-scraper (MIT, copyright 2026 eatmoreduck).

The BOSS adapter reuses its field mapping and visible-JD extraction components
after the user connects a local browser and personally logs in. It does not run
the upstream collection CLI, login-state copying or cookie-export options.
The upstream LICENSE must remain with any redistributed copy.

Our conversational profile, evidence-based preference checks, local API and web
interface are implemented in `app/` for this demo. Original-source attribution is
also shown in the application and project README.

## Markdown lexer

Marked 18.2.0 (MIT) is vendored from the official npm package into
`app/static/vendor/marked/`. Its original LICENSE, source map and artifact
integrity notice are retained. `scripts/vendor_marked.py` checks the npm SHA-512
integrity before preparing these fixed assets.

Only its lexer is used. The application constructs allowed DOM elements itself;
it does not insert Marked's generated HTML. Runtime use needs neither npm nor a
CDN. The upstream archive is a maintainer artifact and is not distributed.
