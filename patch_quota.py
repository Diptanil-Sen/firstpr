import re, pathlib

def edit(path, pairs):
    p = pathlib.Path(path); s = p.read_text()
    for old, new in pairs:
        assert s.count(old) == 1, f"{path}: pattern not found exactly once: {old[:50]}"
        s = s.replace(old, new)
    p.write_text(s)

edit("firstpr/core.py", [
("""    for attempt in range(4):
        try:
            return _client.models.generate_content(model=MODEL, contents=contents, config=config)
        except Exception as e:
            if attempt == 3 or not any(c in str(e) for c in ("429", "500", "503")):
                raise
            time.sleep(2 ** (attempt + 1))""",
"""    for attempt in range(6):
        try:
            return _client.models.generate_content(model=MODEL, contents=contents, config=config)
        except Exception as e:
            s = str(e)
            if attempt == 5 or not any(c in s for c in ("429", "500", "503")):
                raise
            m = re.search(r"retry in ([\\d.]+)s", s)  # the API says how long to wait
            time.sleep(min(float(m.group(1)) + 2, 65) if m else 2 ** (attempt + 1))"""),
("ctx['readme'][:8000]", "ctx['readme'][:4000]"),
("ctx['contributing'][:8000]", "ctx['contributing'][:4000]"),
("ctx['contributing'][:9000]", "ctx['contributing'][:4000]"),
("ctx['template'][:4000]", "ctx['template'][:2500]"),
("ctx['contributing'][:7000]", "ctx['contributing'][:3500]"),
("ctx['template'][:3000]", "ctx['template'][:2000]"),
("[:14000]", "[:6000]"),
])
edit("firstpr/github.py", [
("def _slim(i, n=700):", "def _slim(i, n=400):"),
("def read_file(repo, path, max_chars=12000):", "def read_file(repo, path, max_chars=6000):"),
("def list_files(repo, prefix=\"\", limit=400):", "def list_files(repo, prefix=\"\", limit=200):"),
("if len(issues) >= 25:", "if len(issues) >= 20:"),
("\"issues\": issues[:25]}", "\"issues\": issues[:20]}"),
])
print("patched")
