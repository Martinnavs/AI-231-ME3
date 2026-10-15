import html
import json
import re
from pathlib import Path

import markdown

ROOT = Path("/mnt/jfs_hpc/home/anthony.martin.navarez/ME3/.scratch/project2-voice-agent")
OUT = Path(__file__).with_name("project2-ticket-review.html")
MD_EXT = ["tables", "fenced_code", "sane_lists"]


def md(text: str) -> str:
    mermaids: list[str] = []

    def stash(m: re.Match[str]) -> str:
        mermaids.append(m.group(1))
        return f"\n\nMERMAIDBLOCK{len(mermaids) - 1}\n\n"

    text = re.sub(r"```mermaid\n(.*?)```", stash, text, flags=re.S)
    out = markdown.markdown(text, extensions=MD_EXT)
    for i, src in enumerate(mermaids):
        out = out.replace(
            f"<p>MERMAIDBLOCK{i}</p>",
            f'<div class="diagram"><pre class="mermaid">{html.escape(src)}</pre></div>',
        )
    out = out.replace("<table>", '<div class="tbl"><table>').replace("</table>", "</table></div>")
    return out


def section(text: str, heading: str) -> str:
    m = re.search(rf"^### {re.escape(heading)}\n(.*?)(?=^### |^## |\Z)", text, flags=re.S | re.M)
    return m.group(1) if m else ""


def rows(block: str) -> int:
    lines = [l for l in block.splitlines() if l.startswith("|")]
    return max(len(lines) - 2, 0)


tickets = []
for f in sorted((ROOT / "tickets").glob("*.md")):
    raw = f.read_text()
    title_line = raw.splitlines()[0]
    num, title = re.match(r"# (\d+): (.*)", title_line).groups()
    meta = {}
    for key in ("Repo", "Blocked by", "Status", "Agent"):
        m = re.search(rf"^\*\*{key}:\*\* (.*)$", raw, flags=re.M)
        if m:
            meta[key] = m.group(1)
    body = raw.split("\n", 1)[1]
    body = re.sub(r"^\*\*(Repo|Blocked by|Status|Agent):\*\*.*\n", "", body, flags=re.M)
    body = re.split(r"^## Execution Log", body, flags=re.M)[0]
    sec = re.search(r"^## Security-sensitive\n+(\w+)", raw, flags=re.M)
    files = re.search(r"^## Files\n(.*?)(?=^## )", raw, flags=re.S | re.M)
    tickets.append(
        {
            "id": f"t{num}",
            "num": num,
            "title": title,
            "blocked": meta.get("Blocked by", ""),
            "agent": meta.get("Agent", "developer"),
            "security": bool(sec and sec.group(1).lower().startswith("yes")),
            "files": len([l for l in (files.group(1) if files else "").splitlines() if l.startswith("- ")]),
            "edges": rows(section(raw, "E — Edges")),
            "assumptions": rows(section(raw, "A — Assumptions")),
            "proof": rows(section(raw, "P — Proof")),
            "html": md(body),
        }
    )

design = (ROOT / "DESIGN.md").read_text()
design = design.split("\n", 1)[1]
design = re.split(r"^## Findings index", design, flags=re.M)[0]
plan = (ROOT / "PLAN.md").read_text().split("\n", 1)[1]

decisions = [
    ("D1", "Vendors and models", "Deepgram streaming STT (its end-of-turn signal now replaces the on-Pi turn model), Cartesia streaming TTS, claude-haiku-4-5 as the fast voice model, claude-sonnet-5-5 for the delegated subagent. The professor allows any ASR/TTS as long as TTS is interruptible. Blocks tickets 05 and 10."),
    ("D2", "End of speech from the clean clip", "The benchmark's true end of speech (T0) is the energy boundary of the clean synthesized clip, tested to within 10 ms. No forced aligner. Pause trials use the same rule per part."),
    ("D3", "Web search; simulated calls and messages", "The research subagent uses Anthropic's built-in web search, so the code sandbox never gets network access. Calls and messages are simulated through the ME2 dashboard's phone and logged; real email, SMS and telephony are out of scope."),
    ("D4", "Fast-model provider (after ticket 18)", "Stay on claude-haiku-4-5 unless a challenger (Groq or the class DGX's Qwen) is at least 100 ms faster at median time to first token, within 3 points on overall task success, no task group more than 10 points worse, spoken-format pass of 95% or more, and errors at most 5%, confirmed by a rig run. Decided by you after the paid comparison run."),
]

template = Path(__file__).with_name("review_template.html").read_text()
nav = "\n".join(
    f'<a class="nav-item" href="#{t["id"]}" data-id="{t["id"]}"><span class="nav-num">{t["num"]}</span>'
    f'<span class="nav-title">{html.escape(t["title"])}</span><span class="dot" data-dot="{t["id"]}"></span></a>'
    for t in tickets
)
panels = []
for i, t in enumerate(tickets):
    nxt = tickets[i + 1] if i + 1 < len(tickets) else None
    badges = []
    if t["security"]:
        badges.append('<span class="badge sec">Security review</span>')
    if t["agent"] != "developer":
        badges.append(f'<span class="badge agent">{html.escape(t["agent"].strip("`").split(" ")[0])}</span>')
    panels.append(
        f'''<section class="panel" id="{t["id"]}" hidden>
  <header class="t-head">
    <p class="eyebrow">Ticket {t["num"]} of {len(tickets)}</p>
    <h2>{html.escape(t["title"])}</h2>
    <div class="t-meta">
      <span><b>Blocked by</b> {html.escape(t["blocked"])}</span>
      <span><b>Files</b> {t["files"]}</span>
      {"".join(badges)}
    </div>
    <div class="recap" aria-label="RECAP size">
      <span><b>E</b> {t["edges"]} edges</span><span><b>C</b> contracts</span><span><b>A</b> {t["assumptions"]} assumptions</span><span><b>P</b> {t["proof"]} proofs</span>
    </div>
  </header>
  <div class="fb" data-fb="{t["id"]}"></div>
  <article class="prose">{t["html"]}</article>
  <footer class="t-foot">{f'<a class="next" href="#{nxt["id"]}">Next: {nxt["num"]} {html.escape(nxt["title"])} →</a>' if nxt else '<a class="next" href="#summary">Review summary →</a>'}</footer>
</section>'''
    )

dec_html = "\n".join(
    f'''<div class="decision"><div class="d-text"><p class="eyebrow">{d}</p><h3>{html.escape(title)}</h3><p>{html.escape(desc)}</p></div><div class="fb" data-fb="{d}"></div></div>'''
    for d, title, desc in decisions
)

meta_json = json.dumps(
    [{"id": t["id"], "num": t["num"], "title": t["title"]} for t in tickets]
    + [{"id": d, "num": d, "title": title} for d, title, _ in decisions]
)

page = (
    template.replace("{{NAV}}", nav)
    .replace("{{PANELS}}", "\n".join(panels))
    .replace("{{DECISIONS}}", dec_html)
    .replace("{{DESIGN}}", md(design))
    .replace("{{PLAN}}", md(plan))
    .replace("{{META}}", meta_json)
    .replace("{{COUNT}}", str(len(tickets)))
)
OUT.write_text(page)
print(OUT, len(page))
