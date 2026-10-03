import pathlib
p = pathlib.Path("firstpr/core.py"); s = p.read_text()
pairs = [
('''    for _ in range(max_steps):
        r = _generate(contents, **cfg)
''', '''    for step in range(max_steps):
        if step == 3 and "tools" in cfg:  # enough exploring: force the final answer
            cfg.pop("tools"); cfg.pop("automatic_function_calling")
            contents[-1].parts.append(types.Part(text="Stop exploring. Reply now with ONLY the JSON object."))
        r = _generate(contents, **cfg)
'''),
('''    trace.append("Extracted the repo's contribution rules")
''', '''    trace.append("Extracted the repo's contribution rules")
    issue = gh.get_issue(repo, number)
    files = gh.list_files(repo)["files"][:150]
    trace.append(f"Fetched issue #{number} and the repo file list")
'''),
("First call get_issue and list_files, then read_file on the 1-4 files most relevant. Then return ONLY JSON:",
 "ISSUE: {json.dumps(issue)[:3500]}\\nFILES: {json.dumps(files)}\\n\\nCall read_file on the 1-3 most relevant files (several in ONE step), then return ONLY JSON:"),
]
for old, new in pairs:
    assert s.count(old) == 1, "pattern not found (already patched?): " + old[:40]
    s = s.replace(old, new)
p.write_text(s); print("patched")
