# Charlotte — Design System

Canonical design tokens for the Charlotte knowledge graph UI.
Update this file whenever a token changes; it is the source of truth.

---

## Color Palette

### Light theme (`data-theme="light"`)

| Token | Value | Usage |
|---|---|---|
| `--bg-canvas` | `#F7F5F0` | Page background, graph canvas |
| `--bg-panel` | `#FFFFFF` | Side panel, cards |
| `--bg-topbar` | `#FFFFFF` | Top navigation bar |
| `--border` | `#E5E3DE` | Dividers, input borders |
| `--text-primary` | `#1A1917` | Body text, headings |
| `--text-secondary` | `#6B6860` | Labels, captions, hints |
| `--accent` | `#2563EB` | Links, active states, focus rings |
| `--accent-hover` | `#1D4ED8` | Hovered accent elements |
| `--link-color` | `#ccc8c0` | Graph edge lines (default) |
| `--link-color-highlight` | `#8a8680` | Graph edge lines (highlighted on hover) |

### Dark theme (`data-theme="dark"`)

| Token | Value | Usage |
|---|---|---|
| `--bg-canvas` | `#0f1114` | Page background, graph canvas |
| `--bg-panel` | `#1a1d21` | Side panel, cards |
| `--bg-topbar` | `#1a1d21` | Top navigation bar |
| `--border` | `#2d3139` | Dividers, input borders |
| `--text-primary` | `#d1d5db` | Body text, headings |
| `--text-secondary` | `#9ca3af` | Labels, captions, hints |
| `--accent` | `#60a5fa` | Links, active states, focus rings |
| `--accent-hover` | `#93c5fd` | Hovered accent elements |
| `--link-color` | `#3a4050` | Graph edge lines (default) |
| `--link-color-highlight` | `#7b8494` | Graph edge lines (highlighted on hover) |

### Theme switching

The theme is set on `<html data-theme="light|dark">`. On first load, the user's saved preference is read from `localStorage` key `charlotte-theme`; if absent, OS preference (`prefers-color-scheme`) is used. A toggle button lets the user override it manually — the choice is persisted to `localStorage`.

---

## Node Type Colors

Each node type has a distinct color that reads well in both themes.

| Node Type | Light | Dark | CSS Token |
|---|---|---|---|
| Person | `#0F7075` | `#6ba3be` | `--color-person` |
| Event | `#C2581A` | `#e07850` | `--color-event` |
| Concept | `#6B4FA0` | `#9b8ec4` | `--color-concept` |
| Organization | `#1A7A4A` | `#5eb88a` | `--color-org` |
| Document | `#B45309` | `#c9a84c` | `--color-doc` |

Node fills are read dynamically via `getNodeColor(type)` which calls `getComputedStyle` — this ensures colors update instantly when the theme toggles without re-rendering the graph.

Node strokes use `stroke: var(--bg-canvas)` so they blend naturally with the canvas background in both themes.

---

## Node Shapes

All nodes are rendered as **circles**. Type differentiation is by color only (see Node Type Colors above). Distinct D3 symbol shapes (diamond, hexagon, square, triangle) were considered but retired in favor of simpler circle rendering.

> **Accessibility note:** Color-only differentiation means colorblind users cannot distinguish node types at a glance. The legend and panel type badge provide a fallback. A future improvement could add texture patterns or border dash styles as a secondary signal.

---

## Node Sizing

Node radius scales with connection count (degree):

```
radius = clamp(8, 22, 6 + degree × 1.8)
```

- Leaf nodes (degree 0–1): ~8–10px
- Mid-nodes (degree 3–5): ~13–15px
- Hub nodes (degree 8+): 22px (max)

Each node group also renders a transparent `.node-hit-area` circle at `max(22, nodeRadius)` radius to ensure a minimum 44px tap target on mobile without affecting the visual size.

---

## Typography

Fonts in use: General Sans (body, `--font-body`), Cabinet Grotesk (display headings, `--font-display`). Inter and DM Mono remain as system fallbacks.

| Role | Font | Weight | Size |
|---|---|---|---|
| UI body | General Sans | 400 | `var(--text-sm)` (clamp 14–16px) |
| Headings | General Sans | 600 | varies |
| Panel title | Cabinet Grotesk | 700 | `var(--text-lg)` (clamp 18–24px) |
| Labels (badges, legend) | General Sans | 700 | `var(--text-xs)` (clamp 12–14px) |
| Graph node labels | General Sans | 500 | 11px |

Node labels longer than 22 characters are truncated with `…` in the graph. Full labels are shown in the panel and as `title` tooltips.

---

## Spacing Scale

| Token | Value | Usage |
|---|---|---|
| `--radius` | `8px` | Button and input border radius |
| `--panel-w` | `380px` | Side panel width (desktop, resizable 220–700px) |
| Panel padding | `1.5rem` (`var(--space-6)`) | Side panel inner padding |
| Top bar height | `52px` | Fixed top navigation height |
| Gap (small) | `0.5rem` | Between tight elements |
| Gap (medium) | `0.75–1rem` | Section spacing |

---

## Graph Physics

D3 force simulation parameters:

| Force | Setting |
|---|---|
| Link distance | 120px |
| Link strength | 0.4 |
| Charge strength | −400 |
| Charge max distance | 500px |
| Centering X/Y | strength 0.04 (soft centering) |
| Collision radius | `nodeRadius(d) + 15px` |
| Alpha decay | 0.05 |
| Alpha min | 0.001 |

The soft X/Y centering forces (`strength: 0.04`) spread nodes further apart than a pure center force, producing a more open perplexity-style layout.

---

## Interaction States

### Graph canvas

| State | Visual |
|---|---|
| Default | All nodes + edges at full opacity |
| Node hovered | Hovered node + 1-hop neighbors visible; others dimmed to 10% via `.dimmed` CSS class |
| Node selected | Selected neighborhood at full opacity via inline style; others at 20%; hover highlight disabled |
| Search active | Matching nodes at full opacity; non-matching at 8% via inline style |
| Node dragged | Simulation restarts; node follows cursor |

**Important:** Hover uses CSS classes (`.dimmed`) while selection uses D3 inline `style('opacity', ...)`. Inline styles take precedence over classes, so selection correctly overrides hover. When clearing selection, use `style('opacity', null)` (not `style('opacity', 1)`) to remove inline styles and let CSS classes resume control.

### Side panel

| State | Trigger | Content |
|---|---|---|
| Default (empty) | No node selected | Hint text |
| Content | Node clicked | Pre-generated description from graph data (instant) |
| Expand spinner | Expand button clicked | Loading spinner on selected node |

Node descriptions are generated during the SURVEY stage of graph creation and stored on the node object. Clicking a node shows the description instantly — no second API call is made.

### Connection navigation

Each connection item in the panel is clickable. Clicking pans and zooms the graph viewport to center on the target node and selects it. The pan uses a 500ms D3 transition on the zoom transform.

### Loading (graph generation)

The pipeline takes 30–70 seconds total (including web search). During loading:
- A constellation animation pulses
- **Stage 0** ("Searching the web…") is shown at `t=0` **immediately** and advances to stage 1 when the `fetch()` call resolves (not on a timer). This makes the search visible to users as a distinct, real step.
- **Stages 1–4** ("Surveying entities…" → "Building connections…" → "Validating graph…" → "Finalizing…") cycle on a ~12s interval timer that starts after the server response arrives.
- If `TAVILY_API_KEY` is not set, the server skips search but the frontend still shows stage 0 briefly before the response returns.

---

## Edge Rendering

- Stroke: `var(--link-color)` (set via CSS, not D3 inline attributes)
- Width: 1.5px default; 2.5px when highlighted
- Opacity: controlled by `.dimmed` / `.highlighted` CSS classes and D3 inline styles
- Arrowhead: directional marker (`#arrow`) using `context-stroke` fill; `markerUnits: userSpaceOnUse`; 7×7 viewport
- Relationship labels (`.link-label`): hidden by default; shown on node hover for connected edges via `.visible` CSS class

---

## Mobile Layout

At ≤768px:
- `#main-content` switches to `flex-direction: column`
- Side panel becomes full-width, max-height 55vh, anchored below the graph
- Legend is hidden
- Topic input and search input shrink to 160px / 140px

---

---

## Info Bar

A thin bar below the top bar, shown after a graph first renders.

| Property | Value |
|---|---|
| Height | 32px desktop / 44px mobile |
| Background | `var(--bg-topbar)` |
| Border | `border-bottom: 1px solid var(--border)` |
| Layout | `flexbox`, `space-between` |
| Left content | "Generated Mar 18, 2026" — `var(--text-xs)`, `var(--text-secondary)`, `var(--font-body)` |
| Right content | "↺ Regenerate" — `.btn-ghost.btn-sm` |
| Mobile | Timestamp hidden; Regenerate button only, right-aligned; `min-height: 44px` touch target |
| Accessibility | `role="status"` + `aria-live="polite"` for screen reader announcement |

---

## Ghost Button (`.btn-ghost`)

Used for low-emphasis actions such as Regenerate.

| State | Style |
|---|---|
| Default | `background: transparent`, `border: 1px solid var(--border)`, `color: var(--text-secondary)` |
| Hover | `border-color: var(--accent)`, `color: var(--accent)` |
| Focus-visible | `outline: 2px solid var(--accent)`, `outline-offset: 2px` |
| Disabled / loading | `opacity: 0.4`, `cursor: not-allowed`, `aria-disabled="true"` |

---

## Sources Section (Panel)

Shown inside the node side panel when a node has `source_urls`.

- Heading: `<h3 class="section-label">SOURCES</h3>` — matches CONNECTIONS treatment (same caps, same weight)
- List: `<ul class="sources-list">` with `<li><a>` per citation
- Citation link: `domain.com ↗` (stripped `www.`), `color: var(--accent)`, `var(--text-xs)`
- Max 4 links shown per node
- `target="_blank" rel="noopener noreferrer"` on all links
- Section hidden when `source_urls.length === 0`

---

## Export

| Format | Background |
|---|---|
| PNG | Current `--bg-canvas` (adapts to active theme) |
| SVG | Transparent (inherits page background) |
| JSON | Raw graph data (nodes + edges + topic) |
