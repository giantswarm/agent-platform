#!/usr/bin/env python3
"""Render the README architecture diagram, docs/img/architecture-{light,dark}.svg.

Edit this file, then run: python3 docs/img/architecture.py
"""
from pathlib import Path
from xml.sax.saxutils import escape

THEMES = {
    "light": dict(text="#1f2328", muted="#57606a", panel="#f6f8fa", panel_stroke="#d0d7de",
                  box="#ffffff", line="#57606a", agent="#8250df", tool="#0969da",
                  model="#1a7f37", bar="#ddf4ff", bar_stroke="#54aeff"),
    "dark": dict(text="#e6edf3", muted="#8b949e", panel="#161b22", panel_stroke="#30363d",
                 box="#0d1117", line="#8b949e", agent="#a371f7", tool="#4493f8",
                 model="#3fb950", bar="#121d2f", bar_stroke="#1f6feb"),
}

W, H = 1120, 572
FONT = '-apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif'
BOX_W, BOX_H = 220, 72
COLS = [280, 560, 840]          # box x positions inside a pillar row
ROWS = {"tool": 40, "agent": 200, "model": 360}  # pillar panel tops
PANEL_H = 128


def box_y(row):
    return ROWS[row] + 36


def render(t):
    out = []
    add = out.append

    def text(x, y, s, size=12, weight=400, fill=None, anchor="middle"):
        add(f'<text x="{x}" y="{y}" text-anchor="{anchor}" font-size="{size}" '
            f'font-weight="{weight}" fill="{fill or t["text"]}">{escape(s)}</text>')

    def box(x, y, title, lines, accent, w=BOX_W, h=BOX_H):
        add(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="8" fill="{t["box"]}" '
            f'stroke="{accent}" stroke-width="1.5"/>')
        cx = x + w / 2
        top = y + h / 2 - (len(lines) * 15) / 2 + 2
        text(cx, top, title, 14, 600)
        for i, line in enumerate(lines):
            text(cx, top + 17 + i * 15, line, 11.5, fill=t["muted"])

    def arrow(d, color=None, label=None, lx=0, ly=0, anchor="start", dashed=False):
        color = color or t["line"]
        dash = ' stroke-dasharray="6 4"' if dashed else ""
        add(f'<path d="{d}" fill="none" stroke="{color}" stroke-width="1.5"{dash} '
            f'marker-end="url(#a-{color[1:]})"/>')
        if label:
            text(lx, ly, label, 11, fill=color, anchor=anchor)

    colors = {t["line"], t["agent"], t["tool"], t["model"]}
    add(f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" '
        f'viewBox="0 0 {W} {H}" font-family=\'{FONT}\'>')
    add("<defs>")
    for c in colors:
        add(f'<marker id="a-{c[1:]}" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="8" '
            f'markerHeight="8" orient="auto-start-reverse"><path d="M0,0 L8,4 L0,8 z" fill="{c}"/></marker>')
    add("</defs>")

    # Pillar panels
    for row, label, accent in [("tool", "TOOL ACCESS", t["tool"]),
                               ("agent", "AGENT RUNTIME", t["agent"]),
                               ("model", "MODEL RUNTIME", t["model"])]:
        y = ROWS[row]
        add(f'<rect x="250" y="{y}" width="840" height="{PANEL_H}" rx="12" fill="{t["panel"]}" '
            f'stroke="{t["panel_stroke"]}"/>')
        text(266, y + 22, label, 11, 700, accent, "start")

    # Clients
    text(115, 30, "WHO USES IT", 11, 700, t["muted"])
    box(30, box_y("tool"), "MCP clients", ["Claude Code, Cursor, …"], t["line"], w=170)
    box(30, 188, "Backstage portal", ["the human frontend"], t["line"], w=170)
    box(30, box_y("agent") + 36, "Slack", ["via klaus-gateway"], t["line"], w=170)

    # Tool Access
    ty = box_y("tool")
    box(COLS[0], ty, "agentgateway", ["MCP · A2A · gRPC edge"], t["tool"])
    box(COLS[1], ty, "muster", ["one MCP endpoint", "per-user sign-in · toolsets"], t["tool"])
    box(COLS[2], ty, "MCP servers", ["Kubernetes · agent-manager", "model-manager · …"], t["tool"])

    # Agent Runtime
    ay = box_y("agent")
    box(COLS[0], ay, "kagent", ["agents, templates and", "sessions as CRDs"], t["agent"])
    box(COLS[1], ay, "Agent Substrate", ["each agent in its own", "gVisor sandbox"], t["agent"])
    box(COLS[2], ay, "Snapshots", ["idle agents suspend to", "object storage, then resume"], t["agent"])

    # Model Runtime
    my = box_y("model")
    box(COLS[0], my, "Hosted providers", ["Anthropic · OpenAI · Gemini"], t["model"])
    box(COLS[1], my, "Models gateway", ["agentgateway", "checks the caller's token"], t["model"])
    box(COLS[2], my, "llm-d on KServe", ["your GPUs", "benchmarked serving presets"], t["model"])

    mid = BOX_H / 2
    # Clients into the platform
    arrow(f"M200,{ty + mid} L{COLS[0]},{ty + mid}")
    arrow(f"M200,212 L240,212 L240,{ty + 56} L{COLS[0]},{ty + 56}")
    arrow(f"M200,232 L240,232 L240,{ay + 22} L{COLS[0]},{ay + 22}")
    arrow(f"M200,{ay + 72} L260,{ay + 72} L260,{ay + 54} L{COLS[0]},{ay + 54}")

    # Along each row
    arrow(f"M{COLS[0] + BOX_W},{ty + mid} L{COLS[1]},{ty + mid}")
    arrow(f"M{COLS[1] + BOX_W},{ty + mid} L{COLS[2]},{ty + mid}")
    arrow(f"M{COLS[0] + BOX_W},{ay + mid} L{COLS[1]},{ay + mid}", t["agent"])
    arrow(f"M{COLS[1] + BOX_W},{ay + mid} L{COLS[2]},{ay + mid}", t["agent"])
    arrow(f"M{COLS[1] + BOX_W},{my + mid} L{COLS[2]},{my + mid}", t["model"])

    # Agents call tools and models
    cx = COLS[1] + BOX_W / 2
    arrow(f"M{cx},{ay} L{cx},{ty + BOX_H}", t["tool"], "tools, as the person who asked",
          cx + 10, (ay + ty + BOX_H) / 2 + 4)
    arrow(f"M{cx},{ay + BOX_H} L{cx},{my}", t["model"], "inference", cx + 10,
          (ay + BOX_H + my) / 2 + 4)
    arrow(f"M{cx - 50},{ay + BOX_H} L{COLS[0] + BOX_W / 2 + 40},{my}", t["model"])

    # Foundation bar
    add(f'<rect x="30" y="512" width="1060" height="40" rx="10" fill="{t["bar"]}" '
        f'stroke="{t["bar_stroke"]}"/>')
    text(560, 537, "One OIDC identity end to end  ·  delivered and kept current by Flux  ·  "
         "runs on your Kubernetes", 13, 600)
    add("</svg>")
    return "\n".join(out) + "\n"


if __name__ == "__main__":
    here = Path(__file__).parent
    for name, theme in THEMES.items():
        (here / f"architecture-{name}.svg").write_text(render(theme))
