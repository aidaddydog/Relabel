
import os, json, hashlib, datetime, re
from typing import Iterable

def ensure_dirs(*paths: str):
    for p in paths:
        os.makedirs(p, exist_ok=True)

def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()

def list_zip_dates(zips_dir: str):
    dates = []
    if not os.path.isdir(zips_dir):
        return []
    for name in os.listdir(zips_dir):
        if re.match(r"^pdfs-\d{8}\.zip$", name):
            ymd = name[5:13]
            try:
                dt = datetime.datetime.strptime(ymd, "%Y%m%d").date()
                dates.append(dt.strftime("%Y-%m-%d"))
            except Exception:
                pass
    dates.sort(reverse=True)
    return dates

def sse_event(data: dict) -> bytes:
    payload = "data: " + json.dumps(data, ensure_ascii=False) + "\n\n"
    return payload.encode("utf-8")
