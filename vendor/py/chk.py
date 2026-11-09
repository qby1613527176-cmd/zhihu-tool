"""Check a saved token against the two-endpoint acceptance oracle."""

import json
import sys

sys.path.insert(0, "py")
import zseck as z

path = sys.argv[1] if len(sys.argv) > 1 else ".probe/golden-2.json"
tok = json.load(open(path))
tok = tok["zse"] if isinstance(tok, dict) else tok
print("len", len(tok))
payload, meta = z.parse(tok)
print("payload", len(payload), "meta", len(meta))
print(json.dumps(z.check_token(tok), indent=1, ensure_ascii=False))
