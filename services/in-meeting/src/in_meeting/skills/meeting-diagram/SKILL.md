---
name: meeting-diagram
description: Draw a fast, clear architecture or flow diagram as inline SVG inside a meeting artifact. Use when a picture of how the system fits together lands better than words.
---

# Drawing a meeting diagram

A diagram carries architecture and flow faster than talking. Draw it as inline SVG inside the
HTML artifact (see the `meeting-artifact` skill) so it renders standalone — no image URLs, no
network, no build.

## When
- Explaining how components fit together, a request flow, a data path, a sequence.
- The room would "get it" faster from a picture than from a paragraph.

## Principles
- Draw THIS system: real component names (`control-plane`, `workroom`, `session_host`), real
  edges. Never a generic box-and-arrow that could be any system.
- Clear over fancy: a few labeled boxes and arrows that read at a glance beat a dense graph.
- Match the artifact's dark theme (light strokes/text on the dark page).

## Inline SVG pattern
Boxes as `<rect>` + `<text>`; arrows as `<line>`/`<path>` with an arrowhead `<marker>`.

```html
<svg viewBox="0 0 640 180" width="100%" font-family="sans-serif" font-size="13">
  <defs><marker id="a" markerWidth="8" markerHeight="8" refX="7" refY="3" orient="auto">
    <path d="M0,0 L7,3 L0,6 Z" fill="#7ee787"/></marker></defs>
  <g fill="#161b22" stroke="#30363d">
    <rect x="20"  y="60" width="150" height="52" rx="8"/>
    <rect x="245" y="60" width="150" height="52" rx="8"/>
    <rect x="470" y="60" width="150" height="52" rx="8"/>
  </g>
  <g fill="#e6edf3" text-anchor="middle">
    <text x="95"  y="90">control-plane</text>
    <text x="320" y="90">workroom (E2B)</text>
    <text x="545" y="90">session_host</text>
  </g>
  <g stroke="#7ee787" marker-end="url(#a)">
    <line x1="170" y1="86" x2="243" y2="86"/>
    <line x1="395" y1="86" x2="468" y2="86"/>
  </g>
</svg>
```

For a sequence or a branchy flow where SVG hand-layout gets fiddly, a **mermaid-as-text**
block inside a `<pre class="mermaid">` is fine too — write the mermaid source as text; keep
it simple (`graph LR` / `sequenceDiagram`). Prefer inline SVG when you want it to render with
zero dependencies. Either way: real names, few elements, readable in one glance.
