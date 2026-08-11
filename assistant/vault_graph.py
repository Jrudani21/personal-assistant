"""Interactive 3D knowledge graph of the Obsidian brain vault.

Builds a self-contained HTML page using the bundled 3d-force-graph library
(three.js WebGL, fully local — no CDN at runtime) and vault data from
assistant/vault.py. Nodes are notes, links are [[wiki-links]]. Unresolved
links become hollow "ghost" nodes (the vault's to-write list, per
brain/meta/Conventions.md).

Readability rules baked in (verified against a headless-Chrome screenshot):
- dark background #0E1117 matching the app theme
- every label drawn on a 2D overlay canvas as a dark pill with light text
  (no THREE dependency for labels, so contrast is fully controllable and
  characters always visible over lines/nodes)
- folder -> color mapping, ghost nodes dashed/slate, home node highlighted

Usage:
    python -m assistant.vault_graph --write vault_graph.html   # standalone file
    python -m assistant.vault_graph                            # print HTML (Streamlit embeds it)
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from . import vault

ASSETS = Path(__file__).resolve().parent.parent / "assets" / "vault_graph"
GRAPH_JS = ASSETS / "3d-force-graph.min.js"

# Folder -> color (visible on #0E1117, distinct from each other)
FOLDER_COLORS = {
    "": "#7C9CFF",            # root / home
    "meta": "#F5C518",
    "notes": "#4ADE80",
    "projects": "#FB923C",
    "inbox": "#F87171",
    "templates": "#94A3B8",
    "daily": "#C084FC",
    "refs": "#22D3EE",
}
FALLBACK_COLOR = "#7C9CFF"
GHOST_COLOR = "#94A3B8"
HOME_NAME = "_home"


def _folder_color(folder: str) -> str:
    return FOLDER_COLORS.get(folder, FALLBACK_COLOR)


def build_graph() -> dict:
    """nodes: {id, name, folder, color, snippet}; links: {source, target}."""
    notes = vault.list_notes()
    by_name = {n["name"]: n for n in notes}
    nodes: list[dict] = []
    node_ids: set[str] = set()
    for n in notes:
        color = "#F5C518" if n["name"] == HOME_NAME else _folder_color(n["folder"])
        nodes.append({
            "id": n["rel"],
            "name": n["name"],
            "folder": n["folder"] or "root",
            "color": color,
            "snippet": (n["snippet"] or "")[:220],
        })
        node_ids.add(n["rel"])

    links: list[dict] = []
    seen: set[tuple[str, str]] = set()
    ghosts: dict[str, dict] = {}
    for n in notes:
        for target in n["links"]:
            t = by_name.get(target)
            if t is None:
                ghost_id = f"ghost:{target}"
                if ghost_id not in {g for g in ghosts} and ghost_id not in node_ids:
                    ghosts[ghost_id] = {
                        "id": ghost_id, "name": target,
                        "folder": "unresolved", "color": GHOST_COLOR,
                        "snippet": "Unresolved [[link]] — write this note, or delete the link.",
                        "ghost": True,
                    }
                src, dst = n["rel"], ghost_id
            else:
                src, dst = n["rel"], t["rel"]
            key = (src, dst) if src < dst else (dst, src)
            if key in seen:
                continue
            seen.add(key)
            links.append({"source": src, "target": dst})

    nodes += list(ghosts.values())
    return {"nodes": nodes, "links": links}


def build_html(graph: dict | None = None, inline_js: bool = True) -> str:
    graph = graph or build_graph()
    data_json = json.dumps(graph, ensure_ascii=False)
    js = GRAPH_JS.read_text(encoding="utf-8") if inline_js and GRAPH_JS.exists() else ""

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Brain vault — 3D graph</title>
<style>
  :root {{
    --bg: #0E1117; --panel: #1A1D27; --border: rgba(124,156,255,.25);
    --text: #E6E6E6; --muted: #9AA3B2; --accent: #7C9CFF;
  }}
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  html, body {{ width: 100%; height: 100%; overflow: hidden; background: var(--bg); color: var(--text); font-family: "Segoe UI", system-ui, sans-serif; }}
  #graph {{ position: fixed; inset: 0; }}
  #labels {{ position: absolute; inset: 0; pointer-events: none; z-index: 4; }}
  .panel {{
    position: absolute; top: 14px; right: 14px; width: 300px; max-height: calc(100% - 130px);
    background: var(--panel); border: 1px solid var(--border); border-radius: 12px;
    padding: 14px 16px; overflow: auto; font-size: 13px; line-height: 1.45;
    box-shadow: 0 8px 30px rgba(0,0,0,.45); display: none; z-index: 5;
  }}
  .panel h3 {{ font-size: 15px; margin-bottom: 2px; color: var(--accent); }}
  .panel .meta {{ color: var(--muted); font-size: 11px; margin-bottom: 8px; }}
  .panel p {{ white-space: pre-wrap; }}
  .legend {{
    position: absolute; left: 14px; bottom: 14px; background: rgba(14,17,23,.82);
    border: 1px solid var(--border); border-radius: 10px; padding: 10px 12px;
    font-size: 11px; color: var(--muted); line-height: 1.8; z-index: 5;
  }}
  .legend b {{ color: var(--text); }}
  .legend .sw {{ display: inline-block; width: 9px; height: 9px; border-radius: 50%; margin-right: 6px; }}
  .hint {{
    position: absolute; left: 14px; top: 14px; color: var(--muted); font-size: 12px;
    background: rgba(14,17,23,.7); padding: 8px 12px; border-radius: 8px; border: 1px solid var(--border); z-index: 5;
  }}
  .close {{ position: absolute; top: 8px; right: 10px; cursor: pointer; color: var(--muted); font-size: 16px; background: none; border: none; }}
  .close:hover {{ color: var(--text); }}
  .count {{ color: var(--muted); font-size: 11px; margin-top: 6px; }}
</style>
</head>
<body>
<div id="graph"></div>
<div class="hint">🖱 drag to rotate · scroll to zoom · click a node</div>
<div class="panel" id="panel">
  <button class="close" onclick="document.getElementById('panel').style.display='none'">✕</button>
  <h3 id="p-title"></h3>
  <div class="meta" id="p-meta"></div>
  <p id="p-body"></p>
</div>
<div class="legend">
  <b>Legend</b><br>
  <span class="sw" style="background:#F5C518"></span>home<br>
  <span class="sw" style="background:#7C9CFF"></span>root / refs<br>
  <span class="sw" style="background:#4ADE80"></span>notes<br>
  <span class="sw" style="background:#FB923C"></span>projects<br>
  <span class="sw" style="background:#F87171"></span>inbox<br>
  <span class="sw" style="background:#C084FC"></span>daily<br>
  <span class="sw" style="background:#94A3B8; border:1px dashed #94A3B8;"></span>unresolved link
  <div class="count" id="count"></div>
</div>
<script>{js}</script>
<script>
const GRAPH = {data_json};
const BG = "#0E1117";
const LABEL_BG = "rgba(20,24,34,0.94)";
const LABEL_TEXT = "#F2F4F8";
const GHOST_TEXT = "#B6C0CE";

const Graph = ForceGraph3D()(document.getElementById("graph"))
  .backgroundColor(BG)
  .graphData(GRAPH)
  .nodeRelSize(6)
  .nodeVal(n => (n.id === "_home.md" ? 2.2 : n.ghost ? 0.7 : 1))
  .nodeLabel(n => n.ghost
    ? `<b>Unresolved:</b> [[{{n.name}}]]`
    : `<b>{{n.name}}</b> · {{n.folder}}<br>{{n.snippet}}`)
  .linkColor(() => "rgba(150,170,210,0.22)")
  .linkWidth(1.1)
  .linkOpacity(0.32)
  .linkDirectionalParticles(0)
  .onNodeClick(n => {{
    if (n.ghost) return;
    document.getElementById("p-title").textContent = n.name;
    document.getElementById("p-meta").textContent = n.folder + (n.snippet ? " · note" : "");
    document.getElementById("p-body").textContent = n.snippet || "(no preview — open in Obsidian)";
    document.getElementById("panel").style.display = "block";
  }});
window.__G = Graph;  // debug handle

// ---------- 2D label overlay (readable text, no THREE needed) ----------
const gl = document.querySelector("canvas");
const ov = document.createElement("canvas");
const octx = ov.getContext("2d");
ov.id = "labels";
document.getElementById("graph").appendChild(ov);

function fitOverlay() {{
  const r = gl.getBoundingClientRect();
  const dpr = window.devicePixelRatio || 1;
  ov.width = Math.max(1, Math.round(r.width * dpr));
  ov.height = Math.max(1, Math.round(r.height * dpr));
  ov.style.width = r.width + "px";
  ov.style.height = r.height + "px";
  octx.setTransform(dpr, 0, 0, dpr, 0, 0);
}}
fitOverlay();
window.addEventListener("resize", () => {{
  fitOverlay();
  const r = gl.getBoundingClientRect();
  Graph.width(r.width).height(r.height);
}});

let hovered = null;
Graph.onNodeHover(n => {{
  hovered = n || null;
  document.getElementById("graph").style.cursor = n ? "pointer" : "grab";
}});

function roundRect(x, y, w, h, r) {{
  octx.beginPath();
  octx.moveTo(x + r, y);
  octx.arcTo(x + w, y, x + w, y + h, r);
  octx.arcTo(x + w, y + h, x, y + h, r);
  octx.arcTo(x, y + h, x, y, r);
  octx.arcTo(x, y, x + w, y, r);
  octx.closePath();
}}

function drawFrame() {{
  octx.clearRect(0, 0, ov.width, ov.height);
  const cam = Graph.camera();
  const dir = cam.up.clone();
  cam.getWorldDirection(dir);
  const cpx = cam.position.x, cpy = cam.position.y, cpz = cam.position.z;
  const w = gl.getBoundingClientRect().width, h = gl.getBoundingClientRect().height;
  octx.font = "600 11px Segoe UI, system-ui, sans-serif";
  octx.textBaseline = "middle";

  GRAPH.nodes.forEach(n => {{
    if (n.x == null) return;
    // cull nodes behind the camera
    const dx = n.x - cpx, dy = n.y - cpy, dz = n.z - cpz;
    if (dx * dir.x + dy * dir.y + dz * dir.z < 0) return;
    const s = Graph.graph2ScreenCoords(n.x, n.y, n.z);
    if (s.x < -80 || s.x > w + 80 || s.y < -40 || s.y > h + 40) return;

    if (n.ghost) {{
      octx.save();
      octx.setLineDash([4, 3]);
      octx.strokeStyle = GHOST_TEXT;
      octx.lineWidth = 1.2;
      octx.beginPath();
      octx.arc(s.x, s.y, 11, 0, Math.PI * 2);
      octx.stroke();
      octx.restore();
    }}

    const tw = octx.measureText(n.name).width;
    const pad = 7, lh = 19;
    const lw = tw + pad * 2;
    const lx = Math.min(Math.max(s.x - lw / 2, 4), w - lw - 4);
    const ly = s.y + (n.ghost ? 16 : 12);

    roundRect(lx, ly, lw, lh, 6);
    octx.fillStyle = hovered && hovered.id === n.id ? "rgba(38,46,64,0.98)" : LABEL_BG;
    octx.fill();
    octx.strokeStyle = hovered && hovered.id === n.id ? "#7C9CFF" : (n.ghost ? "rgba(148,163,184,0.45)" : n.color + "77");
    octx.lineWidth = 1;
    octx.stroke();
    octx.fillStyle = n.ghost ? GHOST_TEXT : LABEL_TEXT;
    octx.textAlign = "center";
    octx.fillText((n.ghost ? "○ " : "") + n.name, lx + lw / 2, ly + lh / 2 + 0.5);
  }});
}}
Graph.onRenderFramePost(drawFrame);

document.getElementById("count").textContent =
  GRAPH.nodes.filter(n => !n.ghost).length + " notes · " +
  GRAPH.nodes.filter(n => n.ghost).length + " unresolved · " +
  GRAPH.links.length + " links";

// initial framing: zoom to fit once laid out
setTimeout(() => {{
  try {{ Graph.zoomToFit(400, 50); }} catch (e) {{}}
}}, 700);
</script>
</body>
</html>"""


def write_standalone(path: Path) -> str:
    html = build_html(inline_js=True)
    path.write_text(html, encoding="utf-8")
    return str(path)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Build the 3D vault graph HTML")
    parser.add_argument("--write", metavar="PATH", help="write standalone HTML file")
    parser.add_argument("--json", action="store_true", help="dump graph JSON only")
    args = parser.parse_args()
    if args.json:
        print(json.dumps(build_graph(), ensure_ascii=False, indent=2))
    elif args.write:
        print("Wrote", write_standalone(Path(args.write)))
    else:
        print(build_html())
