"""Measure FirstPR on real repositories and write eval/results.json.
Usage: python -m firstpr.evaluate [--skills "Python, docs"] [--pause 60] [owner/name ...]"""
import argparse, json, time
from pathlib import Path
from . import core

REPOS = ["pallets/click", "pallets/flask", "psf/requests", "encode/httpx", "tiangolo/fastapi",
         "expressjs/express", "facebook/docusaurus", "sindresorhus/got", "django/django", "google-gemma/cookbook"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("repos", nargs="*")
    ap.add_argument("--local", action="store_true", help="evaluate local Gemma through Ollama")
    ap.add_argument("--skills", default="Python, JavaScript, writing docs")
    ap.add_argument("--pause", type=int, default=60, help="seconds between repos (free-tier quota)")
    a = ap.parse_args()
    core.LOCAL.set(a.local)
    rows = []
    for repo in a.repos or REPOS:
        t0, row = time.time(), {"repo": repo}
        try:
            top = core.analyze(repo, a.skills)["issues"][0]
            out = core.plan(repo, top["number"], a.skills)
            row.update(ok=True, issue=top["number"], seconds=round(time.time() - t0), **out["stats"])
        except Exception as e:
            row.update(ok=False, error=str(e)[:160])
        rows.append(row)
        print(row, flush=True)
        time.sleep(a.pause)
    ok = [r for r in rows if r["ok"]]
    n = len(ok) or 1
    summary = {"repos": len(rows), "completed": len(ok), "model": core.LOCAL_MODEL + " (Ollama)" if a.local else core.MODEL, "date": time.strftime("%Y-%m-%d"),
               "first_pass_clean": round(100 * sum(r["before"] == 0 for r in ok) / n),
               "clean_after_repair": round(100 * sum(r["after"] == 0 for r in ok) / n),
               "paths_real": round(100 * sum(r["paths_ok"] for r in ok) / max(1, sum(r["paths"] for r in ok))),
               "avg_tool_calls": round(sum(r["tool_calls"] for r in ok) / n, 1),
               "avg_seconds": round(sum(r["seconds"] for r in ok) / n)}
    d = Path(__file__).resolve().parent.parent / "eval"
    d.mkdir(exist_ok=True)
    (d / "results.json").write_text(json.dumps({"summary": summary, "rows": rows}, indent=2))
    md = ["| repo | issue | clean first try | clean after repair | real paths | tool calls | seconds |", "|---|---|---|---|---|---|---|"]
    md += [f"| {r['repo']} | #{r['issue']} | {r['before'] == 0} | {r['after'] == 0} | {r['paths_ok']}/{r['paths']} | {r['tool_calls']} | {r['seconds']} |"
           if r["ok"] else f"| {r['repo']} | failed: {r['error']} | | | | | |" for r in rows]
    (d / "results.md").write_text("\n".join(md) + "\n\n" + json.dumps(summary, indent=2))
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
