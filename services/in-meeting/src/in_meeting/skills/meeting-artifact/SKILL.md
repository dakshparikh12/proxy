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
2. Start from the house skeleton below. One file: inline all CSS; render standalone.
3. Show it: `to_meeting(medium="screen", content="<the raw HTML>")` (prefer sending the HTML
   itself — external URLs often refuse to embed).

## The house skeleton — start here every time
This carries the house look: design tokens, two fonts (a UI sans + a mono for code and data),
a restrained palette with ONE accent, and a spacing/type scale. Every artifact inherits it so
a doc, a table, and a diagram all read as one designed system — not one-off AI output. Fill in
the content; add only the few styles THIS page needs. Do not re-theme per artifact.

```html
<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>TITLE</title>
<style>
  :root{
    color-scheme:dark;
    /* palette — one ink, one muted, ONE accent (+ pos/neg only for data) */
    --bg:#0f1216; --surface:#161a20; --surface-2:#1c2129; --border:#272d37;
    --ink:#e7eaee; --muted:#98a2b3; --accent:#8aa0ff; --pos:#6ee7a8; --neg:#ff8f8f;
    /* type — two fonts, no webfont fetch */
    --font-sans:ui-sans-serif,system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;
    --font-mono:ui-monospace,"SF Mono",SFMono-Regular,Menlo,Consolas,monospace;
    --f-0:.82rem; --f-1:1rem; --f-2:1.25rem; --f-3:1.6rem; --f-4:2.2rem;
    /* space scale */
    --s-1:.25rem; --s-2:.5rem; --s-3:.75rem; --s-4:1rem; --s-5:1.5rem; --s-6:2rem; --s-7:3rem;
    --measure:66ch; --radius:10px;
  }
  *{box-sizing:border-box}
  body{margin:0;background:var(--bg);color:var(--ink);
    font:var(--f-1)/1.65 var(--font-sans);padding:var(--s-7) var(--s-6)}
  main{max-width:var(--measure);margin:0 auto}
  h1{font-size:var(--f-4);line-height:1.1;letter-spacing:-.02em;margin:0 0 var(--s-3)}
  h2{font-size:var(--f-2);letter-spacing:-.01em;margin:var(--s-7) 0 var(--s-3);
     padding-bottom:var(--s-2);border-bottom:1px solid var(--border)}
  .lede{font-size:var(--f-2);color:var(--muted);margin:0 0 var(--s-6);max-width:60ch}
  p{margin:var(--s-3) 0} strong{color:#fff}
  a{color:var(--accent);text-underline-offset:2px}
  code{font:var(--f-0)/1.5 var(--font-mono);background:var(--surface-2);
    border:1px solid var(--border);padding:.08em .38em;border-radius:6px}
  pre{background:var(--surface);border:1px solid var(--border);border-radius:var(--radius);
    padding:var(--s-4);overflow:auto;font:var(--f-0)/1.55 var(--font-mono)}
  pre code{background:none;border:0;padding:0}
  table{border-collapse:collapse;width:100%;margin:var(--s-4) 0;font-size:var(--f-0)}
  th,td{text-align:left;padding:var(--s-2) var(--s-3);border-bottom:1px solid var(--border)}
  th{color:var(--muted);font-weight:600;text-transform:uppercase;letter-spacing:.04em;
     font-size:.72rem}
  tbody tr:hover{background:var(--surface)}
  ul,ol{padding-left:1.2em} li{margin:var(--s-2) 0}
  .card{background:var(--surface);border:1px solid var(--border);
    border-radius:var(--radius);padding:var(--s-5)}
  .grid{display:grid;gap:var(--s-4);grid-template-columns:repeat(auto-fit,minmax(220px,1fr))}
  .tag{display:inline-block;font:600 var(--f-0)/1 var(--font-mono);color:var(--accent);
    background:color-mix(in srgb,var(--accent) 14%,transparent);
    border:1px solid color-mix(in srgb,var(--accent) 30%,transparent);
    padding:.3em .55em;border-radius:999px}
  figure{margin:var(--s-5) 0} figure svg{max-width:100%;height:auto}
  figcaption{color:var(--muted);font-size:var(--f-0);margin-top:var(--s-2)}
</style></head><body><main>
  <h1>Title for THIS question</h1>
  <p class="lede">The one-line answer up top — read this if you read nothing else.</p>
  <h2>Section</h2>
  <p>Specifics that matter to this team, with real <code>file:line</code>.</p>
</main></body></html>
```

## Charts and data
When numbers carry the point, chart them — don't just table them. Chart.js from a pinned CDN:

```html
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js"></script>
<figure><canvas id="c" height="140"></canvas></figure>
<script>
  const css = getComputedStyle(document.documentElement);
  new Chart(document.getElementById('c'), {
    type:'bar',
    data:{ labels:['p50','p95','p99'],           // real labels from the meeting/repo
           datasets:[{ data:[/* real numbers — never placeholders on screen */],
                       backgroundColor:css.getPropertyValue('--accent').trim() }] },
    options:{ plugins:{legend:{display:false}},
      scales:{ x:{grid:{display:false}, ticks:{color:css.getPropertyValue('--muted')}},
               y:{grid:{color:css.getPropertyValue('--border')},
                  ticks:{color:css.getPropertyValue('--muted')}} } }
  });
</script>
```

That single `<script src>` is the ONE external fetch the house allows. If the render surface
may be offline, draw the chart as inline SVG instead (a few `<rect>`/`<polyline>` — no
dependency). Theme rule: one accent series, muted gridlines, no drop shadows, no 3D, no
rainbow palette for a single series. A deck (only if asked): reveal.js from CDN, one
`<section>` per slide — otherwise a scrolling page is better; don't reach for slides by default.

## Anti-slop checklist — run it before you show the page
This is the difference between "designed" and "AI-generated." Every item:

- **Real content only.** Every number, name, quote, and row comes from THIS meeting or repo.
  No "for example, you might…", no invented sample data, no lorem. If you don't have it, cut
  the section — don't fill it.
- **No bullet-bloat.** Cut any list whose items restate one idea. 2–3 concrete examples beat a
  "comprehensive" ten. Use prose when it's really one thought.
- **No narration.** No "previously this did X", no changelog of your own process, no "as an
  AI". The page states what IS, not the story of making it.
- **No magic numbers.** Every figure is labeled and sourced (unit + where it came from). A
  bare "340ms" with no context is slop.
- **Earn every element.** A section, card, or chart that carries no distinct information gets
  cut. Density of meaning over volume.

Visual tells that scream AI — avoid:
- Buttons/cards that `scale` on hover, tilt, or glow for no reason.
- Animated gradients, particle backgrounds, blur-behind-everything.
- The centered-hero + three-equal-icon-columns layout.
- Every card identical: same 24px padding, same drop shadow, an emoji in each corner.

Instead: a left-aligned reading column, real hierarchy from size/weight/space (not color soup),
borders over shadows, one accent used sparingly.

## Particular to THIS product
Real component names (`control-plane`, `in-meeting`, `workroom`), real `file:line` from the
current clone, real constraints. The test: the page could not have been written for any other
company. Keep it fast — a clean page in one pass beats a fussy one that makes the room wait.
