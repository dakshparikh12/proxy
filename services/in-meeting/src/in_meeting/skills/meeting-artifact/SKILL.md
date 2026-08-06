---
name: meeting-artifact
description: Build a beautiful, self-contained HTML page to show on screen in the meeting — a PRD, plan, report, comparison, or data table. Use when the ask deserves to be SEEN, not just heard.
---

# Building a meeting artifact

When an answer is worth seeing — a document, a plan, a comparison, a table, a report — build
it as ONE self-contained HTML file and show it with `to_meeting` medium `screen`. Say the
gist aloud; the depth lives on the page.

## When to build one
- The content has structure the voice can't carry: sections, a table, a comparison, steps.
- Someone asked for a document/plan/report/mockup, or the room would benefit from seeing it.
- NOT for a quick spoken answer — a crisp sentence stays spoken. Reserve the page for work
  worth looking at.

## How
1. Write the page to a file in your sandbox (e.g. `artifacts/<name>.html`).
2. One file, no build step, no network: inline all CSS in a `<style>` block; no external
   fonts, scripts, or images. It must render standalone.
3. Show it: `to_meeting(medium="screen", content="<the raw HTML>")` (prefer sending the HTML
   itself — external URLs often refuse to embed).

## Structure
- A clear `<h1>` title and a one-line summary up top (the "if you read nothing else").
- Real sections with `<h2>`s; short paragraphs; `<table>` for anything tabular; `<ul>` for
  lists. A diagram goes inline as SVG (see the `meeting-diagram` skill) — never an image URL.
- Particular to THIS product: real component names, real `file:line`, real constraints. The
  test: it could not have been written for any other company.

## Dark theme (default)
Legible on a shared screen. Keep it minimal — content over chrome.

```html
<!doctype html><html><head><meta charset="utf-8"><style>
  :root{color-scheme:dark}
  body{margin:0;padding:2.5rem;background:#0d1117;color:#e6edf3;
       font:16px/1.6 -apple-system,Segoe UI,Roboto,sans-serif;max-width:60rem}
  h1{font-size:1.9rem;margin:0 0 .25rem} h2{margin:2rem 0 .5rem;color:#7ee787}
  .lede{color:#9da7b3;font-size:1.05rem;margin-bottom:1.5rem}
  table{border-collapse:collapse;width:100%;margin:1rem 0}
  th,td{border:1px solid #30363d;padding:.5rem .75rem;text-align:left}
  th{background:#161b22} code{background:#161b22;padding:.1rem .3rem;border-radius:4px}
  a{color:#58a6ff}
</style></head><body>
  <h1>Title for THIS question</h1>
  <p class="lede">The one-line answer up top.</p>
  <h2>Section</h2><p>Specifics that matter to this team…</p>
</body></html>
```

Keep it fast: a clean page in one pass beats a fussy one that makes the room wait.
