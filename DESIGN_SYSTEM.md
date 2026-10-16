# Design system - KEN web UI (`ken.html`)

The UI is one large hand-written file, `ken.html` (about 4,100 lines: CSS, markup, JS), served
by `server.py` and wrapped by the Electron app in `desktop/`. No CSS framework or build step.
It shares its palette with the owner's portfolio site (same `#05050a` / `#ff3131` / `#59d9ff`
/ `#f5c518` values).

## Tokens (`:root`, `ken.html` lines 15-35)

| Token | Value | Notes |
|---|---|---|
| `--bg` | `#05050a` | app background |
| `--panel` / `--panel2` | `#0a0a12` / `#100f16` | surfaces |
| `--panel3` | `rgba(255,49,49,.06)` | accent-tinted surface |
| `--border` / `--border2` | `rgba(255,255,255,.07)` / `rgba(255,49,49,.12)` | |
| `--ink` | `#f2f2f5` | text |
| `--muted` / `--muted2` | `rgba(242,242,245,.55)` / `.32` | secondary text |
| `--accent` | `#ff3131` | primary accent; `--ok` uses the same value |
| `--accent-tint` | `rgba(255,49,49,.12)` | |
| `--pink` / `--err` | `#ff6b6b` | errors |
| `--pink-tint` | `rgba(255,107,107,.25)` | |
| `--violet` | `#59d9ff` | value is cyan despite the name |
| `--amber` | `#f5c518` | |
| `--glow` | `0 0 24px rgba(255,49,49,.3)` | |

Note `--ok` and `--accent` are identical red, so success and accent states are not
distinguishable by colour alone.

## Typography

`--display: 'Michroma'`, `--body: 'Space Grotesk'`, `--mono: 'VT323', 'JetBrains Mono', ...`;
loaded from Google Fonts (Michroma, Space Grotesk, VT323, JetBrains Mono).

## Other UI assets

- `assets/three.min.js` (vendored three.js) loaded by `ken.html` for 3D visuals
  (particle field; degrades if the script is missing). `verify_ken3d.py` checks `ken.html` CRLF
  integrity and inline-JS syntax after the 3D edit.
- `assets/vault_graph/force-graph.min.js` and `3d-force-graph.min.js` (vendored) for the vault
  graph view.
- Dark theme only: no `prefers-color-scheme` or theme switch was found.
- `pages/1_Settings.py` is a separate Streamlit page with its own default styling (see PRD.md:
  likely orphaned).

## Verified against

`ken.html` (`:root`, Google Fonts link, `<script src>` tags, `grep prefers-color-scheme`),
`wc -l ken.html`, `desktop/main.js`, `verify_ken3d.py` name, portfolio `index.html` tokens.
