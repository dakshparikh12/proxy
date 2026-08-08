---
name: meeting-diagram
description: Draw a fast, clear architecture or flow diagram inside a meeting artifact — Mermaid by default, inline SVG when you want zero dependency. Use when a picture of how the system fits together lands better than words.
---

# Drawing a meeting diagram

A diagram carries architecture and flow faster than talking. Draw it inside the HTML artifact
(see the `meeting-artifact` skill), themed to the SAME house tokens, so a diagram and a doc
read as one system.

## When
- Explaining how components fit together, a request flow, a data path, a sequence.
- The room would "get it" faster from a picture than from a paragraph.

## Mermaid (default)
Best for sequences and branchy flows. Theme it to the house palette with an `init` directive
so it matches the artifact. The `<script src>` is the one external fetch we allow (same rule
as charts) — if the render surface may be offline, use the inline-SVG pattern below instead.

```html
<pre class="mermaid">
%%{init:{'theme':'base','themeVariables':{
  'background':'#0f1216','primaryColor':'#161a20','primaryBorderColor':'#272d37',
  'primaryTextColor':'#e7eaee','lineColor':'#8aa0ff','fontFamily':'ui-sans-serif,system-ui'}}}%%
flowchart LR
  cp[control-plane] --> im[in-meeting]
  im -->|transcript| wr[workroom · E2B sandbox]
  wr -->|to_meeting| cp
</pre>
<script src="https://cdn.jsdelivr.net/npm/mermaid@11/dist/mermaid.min.js"></script>
<script>mermaid.initialize({startOnLoad:true});</script>
```

## Inline SVG (zero dependency)
When you want it to render with no network at all, or need precise control. Boxes as
`<rect>` + `<text>`, arrows as `<line>` with an arrowhead `<marker>`. Same house colors.

```html
<svg viewBox="0 0 640 120" width="100%" font-family="ui-sans-serif,system-ui" font-size="13">
  <defs><marker id="a" markerWidth="8" markerHeight="8" refX="7" refY="3" orient="auto">
    <path d="M0,0 L7,3 L0,6 Z" fill="#8aa0ff"/></marker></defs>
  <g fill="#161a20" stroke="#272d37" rx="8">
    <rect x="20"  y="40" width="150" height="48" rx="8"/>
    <rect x="245" y="40" width="150" height="48" rx="8"/>
    <rect x="470" y="40" width="150" height="48" rx="8"/>
  </g>
  <g fill="#e7eaee" text-anchor="middle">
    <text x="95"  y="69">control-plane</text>
    <text x="320" y="69">workroom (E2B)</text>
    <text x="545" y="69">session_host</text>
  </g>
  <g stroke="#8aa0ff" marker-end="url(#a)">
    <line x1="170" y1="64" x2="243" y2="64"/>
    <line x1="395" y1="64" x2="468" y2="64"/>
  </g>
</svg>
```

## Principles (fold in the anti-slop visual rules)
- **Real names, real edges.** Draw THIS system: `premeeting`, `control-plane`, `in-meeting`,
  `workroom`, the E2B sandbox, `to_meeting`, Recall/AssemblyAI/Cartesia, Postgres + GCS. Never
  a generic box-and-arrow that could be any system.
- **Label edges with what actually flows** — `transcript`, `to_meeting`, `signed push`, not a
  bare line.
- **Few nodes (aim 5–9).** If it needs more, you're diagramming too much — split it or zoom in.
- **One accent, neutral boxes.** No per-box rainbow fills, no gradients or drop shadows on
  nodes, no decorative icons. Clarity over decoration — a few labeled boxes that read at a
  glance beat a dense graph.
- **Match the artifact** — same fonts and colors, so the picture belongs to the page.
