"""Print the file a tool touched, from the hook payload on stdin."""
import json
import sys

try:
    d = json.load(sys.stdin)
except Exception:
    sys.exit(0)
tool_in = d.get("tool_input") or {}
resp = d.get("tool_response") or {}
print(tool_in.get("file_path") or (resp.get("filePath") if isinstance(resp, dict) else "") or "")
