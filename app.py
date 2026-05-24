#!/usr/bin/env python3
"""SNS メディアアーカイバ — ローカル管理Webアプリ。

Instagram / X(Twitter) / TikTok / Pinterest / Tumblr / Bluesky のアカウントを
登録し、動画・画像を gallery-dl で一括取得、差分更新を管理する。
"""

import json
import os
import re
import shutil
import subprocess
import threading
import time
import uuid
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

ROOT = os.path.dirname(os.path.abspath(__file__))
MEDIA = os.path.join(ROOT, "media")
DATA = os.path.join(ROOT, "data")
WEB = os.path.join(ROOT, "web")
LOGS = os.path.join(ROOT, "logs", "jobs")
TRASH = os.path.join(ROOT, "trash")
ACCOUNTS_FILE = os.path.join(DATA, "accounts.json")
CONFIG = os.path.join(ROOT, "gdl_config.json")
PORT = 8780

for d in (MEDIA, DATA, WEB, LOGS):
    os.makedirs(d, exist_ok=True)

VIDEO_EXT = {".mp4", ".mkv", ".webm", ".mov"}
IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
MEDIA_EXT = VIDEO_EXT | IMAGE_EXT
FOLDER_RE = re.compile(r"^[A-Za-z0-9._-]{1,90}$")
USER_RE = re.compile(r"^[A-Za-z0-9._-]{1,60}$")
DATE_RE = re.compile(r"(20\d{6})")

# 対応SNS。同じ仕組み（プロフィールURL → 全メディア + 差分アーカイブ）で動く。
PLATFORMS = {
    "instagram": {"label": "Instagram", "domains": ("instagram.com",),
                  "url": "https://www.instagram.com/{u}/"},
    "twitter":   {"label": "X (Twitter)", "domains": ("twitter.com", "x.com"),
                  "url": "https://x.com/{u}"},
    "tiktok":    {"label": "TikTok", "domains": ("tiktok.com",),
                  "url": "https://www.tiktok.com/@{u}"},
    "pinterest": {"label": "Pinterest",
                  "domains": ("pinterest.com", "pinterest.jp"),
                  "url": "https://www.pinterest.com/{u}/"},
    "tumblr":    {"label": "Tumblr", "domains": ("tumblr.com",),
                  "url": "https://{u}.tumblr.com/"},
    "bluesky":   {"label": "Bluesky", "domains": ("bsky.app",),
                  "url": "https://bsky.app/profile/{u}"},
}

jobs = {}
jobs_lock = threading.Lock()
accounts_lock = threading.Lock()


# --------------------------------------------------------------------------
# アカウント永続化
# --------------------------------------------------------------------------
def load_accounts():
    if not os.path.exists(ACCOUNTS_FILE):
        return []
    try:
        with open(ACCOUNTS_FILE, encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return []


def save_accounts(accounts):
    tmp = ACCOUNTS_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(accounts, f, ensure_ascii=False, indent=2)
    os.replace(tmp, ACCOUNTS_FILE)


def find_account(accounts, folder):
    for a in accounts:
        if a.get("folder") == folder:
            return a
    return None


def parse_account(raw):
    """ユーザー名またはプロフィールURLから {platform, username, url} を得る。

    素のユーザー名は Instagram とみなす。URL ならドメインからSNSを判別。
    """
    raw = (raw or "").strip()
    if not raw:
        return None

    low = raw.lower()
    looks_url = "://" in raw or any(
        d in low for p in PLATFORMS.values() for d in p["domains"])

    if looks_url:
        u = raw if "://" in raw else "https://" + raw
        parsed = urlparse(u)
        host = parsed.netloc.lower().split(":")[0]
        if host.startswith("www."):
            host = host[4:]
        platform = None
        for key, p in PLATFORMS.items():
            if any(host == d or host.endswith("." + d) for d in p["domains"]):
                platform = key
                break
        if not platform:
            return None
        parts = [s for s in parsed.path.split("/") if s]
        if platform == "tumblr":
            sub = host.split(".")[0]
            username = sub if sub not in ("tumblr",) and "." in host \
                else (parts[0] if parts else "")
        elif platform == "bluesky":
            username = parts[1] if len(parts) >= 2 and parts[0] == "profile" \
                else (parts[0] if parts else "")
        else:
            username = parts[0] if parts else ""
        username = username.lstrip("@")
    else:
        platform = "instagram"
        username = raw.lstrip("@").strip("/")

    if not username or not USER_RE.match(username):
        return None
    return {
        "platform": platform,
        "username": username,
        "url": PLATFORMS[platform]["url"].format(u=username),
    }


def make_folder(platform, username, existing):
    base = re.sub(r"[^A-Za-z0-9._-]", "_", f"{platform}_{username}")
    folder, i = base, 2
    while folder in existing:
        folder, i = f"{base}_{i}", i + 1
    return folder


# --------------------------------------------------------------------------
# メディア集計
# --------------------------------------------------------------------------
def media_date(name):
    m = DATE_RE.search(name)
    return m.group(1) if m else "00000000"


def count_media(d):
    n = 0
    try:
        for e in os.scandir(d):
            if e.is_file() and \
               os.path.splitext(e.name)[1].lower() in MEDIA_EXT:
                n += 1
    except OSError:
        pass
    return n


def account_stats(folder):
    d = os.path.join(MEDIA, folder)
    videos = images = 0
    total = 0
    latest = 0.0
    if os.path.isdir(d):
        for entry in os.scandir(d):
            if not entry.is_file():
                continue
            ext = os.path.splitext(entry.name)[1].lower()
            if ext not in MEDIA_EXT:
                continue
            st = entry.stat()
            total += st.st_size
            latest = max(latest, st.st_mtime)
            if ext in VIDEO_EXT:
                videos += 1
            else:
                images += 1
    return {"videos": videos, "images": images, "total": videos + images,
            "bytes": total, "last_modified": latest}


def list_media(folder, limit=None, newest=True):
    d = os.path.join(MEDIA, folder)
    items = []
    if os.path.isdir(d):
        for entry in os.scandir(d):
            if not entry.is_file():
                continue
            ext = os.path.splitext(entry.name)[1].lower()
            if ext not in MEDIA_EXT:
                continue
            st = entry.stat()
            items.append({
                "name": entry.name,
                "kind": "video" if ext in VIDEO_EXT else "image",
                "bytes": st.st_size,
                "mtime": st.st_mtime,
                "date": media_date(entry.name),
            })
    items.sort(key=lambda x: (x["date"], x["name"]), reverse=newest)
    if limit:
        items = items[:limit]
    return items


# --------------------------------------------------------------------------
# ダウンロードジョブ
# --------------------------------------------------------------------------
def run_job(job_id, folder):
    job = jobs[job_id]
    account = find_account(load_accounts(), folder)
    if not account:
        job["status"] = "error"
        job["messages"].append("アカウントが見つかりません")
        job["finished"] = time.time()
        return

    media_dir = os.path.join(MEDIA, folder)
    os.makedirs(media_dir, exist_ok=True)
    archive = os.path.join(media_dir, "_archive.sqlite")
    log_path = os.path.join(LOGS, job_id + ".log")

    cmd = [
        "gallery-dl",
        "--config", CONFIG,
        "--cookies-from-browser", "chrome",
        "-D", media_dir,
        "--download-archive", archive,
        account["url"],
    ]

    job["status"] = "running"
    job["started"] = time.time()
    tail = job["log_tail"]

    try:
        with open(log_path, "w", encoding="utf-8") as logf:
            logf.write(" ".join(cmd) + "\n\n")
            logf.flush()
            proc = subprocess.Popen(
                cmd, cwd=ROOT, stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT, text=True, bufsize=1,
            )
            job["pid"] = proc.pid
            base = count_media(media_dir)
            for line in proc.stdout:
                line = line.rstrip("\n")
                logf.write(line + "\n")
                logf.flush()
                stripped = line.strip()
                cand = stripped.lstrip("✔#").strip()
                ext = os.path.splitext(cand)[1].lower()
                if ext in MEDIA_EXT:
                    job["downloaded"] = max(0, count_media(media_dir) - base)
                    if not stripped.startswith("#"):
                        job["current"] = os.path.basename(cand)
                elif "[error]" in line.lower():
                    job["messages"].append(stripped[:200])
                    job["messages"] = job["messages"][-8:]
                tail.append(stripped)
                if len(tail) > 60:
                    del tail[:-60]
            rc = proc.wait()
            job["downloaded"] = max(0, count_media(media_dir) - base)

        if job["status"] == "stopping":
            job["status"] = "stopped"
        elif rc == 0:
            job["status"] = "done"
        else:
            job["status"] = "error"
            job["messages"].append(f"gallery-dl 終了コード {rc}")
    except Exception as exc:                       # noqa: BLE001
        job["status"] = "error"
        job["messages"].append(str(exc)[:200])
    finally:
        job["finished"] = time.time()
        with accounts_lock:
            accounts = load_accounts()
            a = find_account(accounts, folder)
            if a:
                a["last_run"] = job["finished"]
                a["last_added"] = job["downloaded"]
                save_accounts(accounts)


def start_job(folder):
    with jobs_lock:
        for j in jobs.values():
            if j["folder"] == folder and \
               j["status"] in ("queued", "running", "stopping"):
                return None, "このアカウントは既に実行中です"
        account = find_account(load_accounts(), folder)
        if not account:
            return None, "未登録のアカウントです"
        job_id = uuid.uuid4().hex[:12]
        jobs[job_id] = {
            "id": job_id,
            "folder": folder,
            "username": account.get("username", folder),
            "platform": account.get("platform", "instagram"),
            "status": "queued",
            "created": time.time(),
            "started": None,
            "finished": None,
            "downloaded": 0,
            "current": "",
            "pid": None,
            "messages": [],
            "log_tail": [],
        }
    threading.Thread(target=run_job, args=(job_id, folder), daemon=True).start()
    return job_id, None


def stop_job(job_id):
    job = jobs.get(job_id)
    if not job or job["status"] not in ("queued", "running"):
        return False
    job["status"] = "stopping"
    pid = job.get("pid")
    if pid:
        try:
            subprocess.run(["kill", str(pid)], check=False)
        except OSError:
            pass
    return True


def public_job(job):
    return {
        "id": job["id"], "folder": job["folder"],
        "username": job["username"], "platform": job["platform"],
        "status": job["status"], "created": job["created"],
        "started": job["started"], "finished": job["finished"],
        "downloaded": job["downloaded"], "current": job["current"],
        "messages": job["messages"][-4:],
    }


# --------------------------------------------------------------------------
# サムネイル
# --------------------------------------------------------------------------
def thumb_path(folder, name):
    d = os.path.join(MEDIA, folder, ".thumbs")
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, name + ".jpg")


def safe_media_path(folder, name):
    if not FOLDER_RE.match(folder or ""):
        return None
    base = os.path.realpath(os.path.join(MEDIA, folder))
    target = os.path.realpath(os.path.join(base, name))
    if target == base or not target.startswith(base + os.sep):
        return None
    return target if os.path.isfile(target) else None


def trash_folder(folder):
    """media/<folder> をアプリ内 trash/ へ移動する。復元可能。成否を返す。

    完全に消すには trash/ フォルダを手動で空にする。
    """
    if not FOLDER_RE.match(folder or ""):
        return False
    src = os.path.realpath(os.path.join(MEDIA, folder))
    media_root = os.path.realpath(MEDIA)
    if not src.startswith(media_root + os.sep) or not os.path.isdir(src):
        return False
    os.makedirs(TRASH, exist_ok=True)
    dst = os.path.join(TRASH, f"{folder}_{int(time.time())}")
    try:
        shutil.move(src, dst)
        return True
    except (OSError, shutil.Error):
        return False


def ensure_thumb(folder, name):
    src = safe_media_path(folder, name)
    if not src:
        return None
    ext = os.path.splitext(name)[1].lower()
    if ext in IMAGE_EXT:
        return src
    tp = thumb_path(folder, name)
    if os.path.exists(tp) and os.path.getmtime(tp) >= os.path.getmtime(src):
        return tp
    try:
        subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error", "-ss", "0.5", "-i", src,
             "-vframes", "1", "-vf", "scale=400:-1", tp],
            check=True, timeout=30,
        )
        return tp if os.path.exists(tp) else None
    except (subprocess.SubprocessError, OSError):
        return None


# --------------------------------------------------------------------------
# HTTP ハンドラ
# --------------------------------------------------------------------------
class Handler(BaseHTTPRequestHandler):
    server_version = "SnsArchiver/2.0"

    def log_message(self, *args):
        pass

    def send_json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def send_file(self, path, download_name=None):
        if not path or not os.path.isfile(path):
            self.send_error(404)
            return
        ctypes = {
            ".html": "text/html; charset=utf-8",
            ".css": "text/css; charset=utf-8",
            ".js": "application/javascript; charset=utf-8",
            ".mp4": "video/mp4", ".webm": "video/webm", ".mov": "video/quicktime",
            ".mkv": "video/x-matroska",
            ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
            ".webp": "image/webp", ".gif": "image/gif",
        }
        ext = os.path.splitext(path)[1].lower()
        ct = ctypes.get(ext, "application/octet-stream")
        size = os.path.getsize(path)
        self.send_response(200)
        self.send_header("Content-Type", ct)
        self.send_header("Content-Length", str(size))
        self.send_header("Accept-Ranges", "bytes")
        if ext in (".html", ".css", ".js"):
            self.send_header("Cache-Control", "no-store")
        if download_name:
            self.send_header(
                "Content-Disposition", f'attachment; filename="{download_name}"')
        self.end_headers()
        with open(path, "rb") as f:
            shutil.copyfileobj(f, self.wfile)

    def read_body(self):
        length = int(self.headers.get("Content-Length", 0) or 0)
        if not length:
            return {}
        try:
            return json.loads(self.rfile.read(length).decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return {}

    # -- GET --
    def do_GET(self):
        u = urlparse(self.path)
        path, q = u.path, parse_qs(u.query)

        if path in ("/", "/index.html"):
            self.send_file(os.path.join(WEB, "index.html"))
            return
        if path in ("/style.css", "/app.js"):
            self.send_file(os.path.join(WEB, path.lstrip("/")))
            return
        if path == "/api/state":
            self.send_json(self.build_state())
            return
        if path == "/api/media":
            folder = (q.get("acct") or [""])[0]
            if not FOLDER_RE.match(folder):
                self.send_json({"error": "不正な指定"}, 400)
                return
            self.send_json({"acct": folder, "items": list_media(folder)})
            return
        if path == "/thumb":
            tp = ensure_thumb((q.get("acct") or [""])[0],
                              (q.get("name") or [""])[0])
            self.send_file(tp) if tp else self.send_error(404)
            return
        if path == "/file":
            fp = safe_media_path((q.get("acct") or [""])[0],
                                 (q.get("name") or [""])[0])
            dl = (q.get("download") or [""])[0] == "1"
            self.send_file(fp, download_name=(q.get("name") or [None])[0]
                           if dl else None)
            return
        self.send_error(404)

    # -- POST --
    def do_POST(self):
        path = urlparse(self.path).path
        body = self.read_body()

        if path == "/api/account/add":
            info = parse_account(body.get("username"))
            if not info:
                self.send_json(
                    {"error": "ユーザー名またはURLが不正です"}, 400)
                return
            with accounts_lock:
                accounts = load_accounts()
                if any(a.get("platform") == info["platform"] and
                       a.get("username") == info["username"]
                       for a in accounts):
                    self.send_json({"error": "既に登録済みです"}, 400)
                    return
                folder = make_folder(info["platform"], info["username"],
                                     {a.get("folder") for a in accounts})
                accounts.append({
                    "platform": info["platform"],
                    "username": info["username"],
                    "folder": folder,
                    "url": info["url"],
                    "added_at": time.time(),
                    "last_run": None,
                    "last_added": None,
                })
                save_accounts(accounts)
            os.makedirs(os.path.join(MEDIA, folder), exist_ok=True)
            self.send_json({"ok": True, "folder": folder})
            return

        if path == "/api/account/remove":
            folder = body.get("acct", "")
            purge = bool(body.get("purge"))
            with accounts_lock:
                accounts = load_accounts()
                existed = any(a.get("folder") == folder for a in accounts)
                save_accounts([a for a in accounts
                               if a.get("folder") != folder])
            # データ削除指定時はメディアフォルダをゴミ箱へ移動（復元可能）
            trashed = trash_folder(folder) if (purge and existed) else False
            self.send_json({"ok": True, "trashed": trashed})
            return

        if path == "/api/download":
            job_id, err = start_job(body.get("acct", ""))
            if err:
                self.send_json({"error": err}, 409)
            else:
                self.send_json({"ok": True, "job_id": job_id})
            return

        if path == "/api/stop":
            self.send_json({"ok": stop_job(body.get("job_id", ""))})
            return

        self.send_error(404)

    # -- 状態 --
    def build_state(self):
        accounts = load_accounts()
        with jobs_lock:
            job_list = [public_job(j) for j in jobs.values()]
        job_list.sort(key=lambda j: j["created"], reverse=True)
        active = {j["folder"]: j["id"] for j in job_list
                  if j["status"] in ("queued", "running", "stopping")}

        out = []
        for a in accounts:
            folder = a.get("folder")
            out.append({
                **a,
                "platform_label": PLATFORMS.get(
                    a.get("platform", ""), {}).get("label", a.get("platform")),
                "stats": account_stats(folder),
                "preview": [m["name"] for m in list_media(folder, limit=6)],
                "active_job": active.get(folder),
            })
        return {"accounts": out, "jobs": job_list[:12],
                "server_time": time.time()}


def main():
    server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    print(f"SNS メディアアーカイバ起動: http://127.0.0.1:{PORT}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n停止しました")
        server.shutdown()


if __name__ == "__main__":
    main()
