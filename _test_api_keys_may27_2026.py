#!/usr/bin/env python3
import os
import httpx

for p in [
    r"D:\Users\admin2\Desktop\MYCOSOFT\CODE\.env",
    r"D:\Users\admin2\Desktop\MYCOSOFT\CODE\MINDEX\mindex\.env",
    r"D:\Users\admin2\Desktop\MYCOSOFT\CODE\WEBSITE\website\.env.local",
]:
    if os.path.exists(p):
        for line in open(p, encoding="utf-8", errors="ignore"):
            if line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

base = "http://192.168.0.189:8000/api/mindex/observations"
params = {
    "bbox": "-117.45,32.45,-116.85,33.35",
    "kingdom": "Fungi",
    "limit": 20,
    "include_total": "false",
}
api_key = os.environ.get("MINDEX_API_KEY")
if not api_key:
    raise RuntimeError("MINDEX_API_KEY must be set in an ignored env file")

for label, key in [
    ("env_key", api_key),
    ("no_key", None),
]:
    headers = {"Accept": "application/json"}
    if key:
        headers["X-API-Key"] = key
    r = httpx.get(base, params=params, headers=headers, timeout=15)
    rows = len(r.json().get("data", [])) if r.status_code == 200 else 0
    print(label, "status", r.status_code, "rows", rows)
