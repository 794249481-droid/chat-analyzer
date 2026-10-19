Warning: fs was declared with const; use let for reassignable variables.
#!/usr/bin/env python3
"""Web server for the sales chat analysis system. Share with colleagues via LAN."""

import json
import os
import sys
import html
import shutil
import subprocess
import tempfile
import time
import zipfile
import threading
import re
from pathlib import Path
from datetime import datetime
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import parse_qs, urlparse, unquote, quote
from io import BytesIO

import parser as chat_parser
import analyzer as chat_analyzer
import reporter as chat_reporter
import scorer as chat_scorer

# Bind locally by default. Exposing customer analysis on a LAN without
# authentication is unsafe; users can opt in with CHAT_ANALYZER_HOST.
HOST = os.environ.get("CHAT_ANALYZER_HOST", "127.0.0.1")
PORT = 8888
ROOT = Path(__file__).parent
UPLOAD_DIR = ROOT / "uploads"
REPORT_DIR = ROOT / "output"
TASK_STATUS = {}
_task_lock = threading.Lock()
_LATEST_DEALS = []

for d in [UPLOAD_DIR, REPORT_DIR]:
    d.mkdir(exist_ok=True)

ANALYSIS_SESSIONS = {}

HISTORY_FILE = REPORT_DIR / "_history.json"


def _load_history() -> list:
    if HISTORY_FILE.exists():
        try:
            with open(HISTORY_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            pass
    return []


def _save_history(base_name: str, entry: dict):
    history = _load_history()
    # Remove existing entry with same base_name
    history = [h for h in history if h.get("base_name") != base_name]
    history.insert(0, entry)
    # Keep last 50 entries
    history = history[:50]
    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, indent=2)


WEB_MAX_CUSTOMERS = 0  # 0 = no limit, analyze all customers


def run_analysis(session_id: str, input_path: str, input_type: str,
                 original_files: list = None):
    """Run analysis in background thread."""
    original_files = original_files or []
    with _task_lock:
        TASK_STATUS[session_id] = {"status": "running", "progress": 0, "message": "开始分析..."}

    def update_progress(current, total, name):
        pct = int(current / total * 100) if total else 0
        with _task_lock:
            TASK_STATUS[session_id] = {
                "status": "running",
                "progress": pct,
                "message": f"分析中 {current}/{total}: {name}",
            }

    try:
        if input_type == "json":
            parsed = chat_parser.parse_json_export(input_path)
            agent_name = parsed["agent_name"]
            conversations = parsed["conversations"]
            valid = {k: v for k, v in conversations.items() if len(v) >= 2}

            with _task_lock:
                TASK_STATUS[session_id]["message"] = f"解析完成，{len(valid)} 个客户待分析"

            results = chat_analyzer.batch_analyze_json(
                valid, agent_name, min_messages=2,
                progress_callback=update_progress,
                max_items=WEB_MAX_CUSTOMERS,
            )

        elif input_type == "images":
            parsed = chat_parser.parse_chat_folders(input_path)
            agent_name = parsed["agent_name"]
            conversations = parsed["conversations"]

            with _task_lock:
                TASK_STATUS[session_id]["message"] = f"扫描完成，{len(conversations)} 个客户"

            results = chat_analyzer.batch_analyze_images(
                conversations, agent_name,
                progress_callback=update_progress,
                max_items=WEB_MAX_CUSTOMERS,
            )

        elif input_type == "image_folder":
            info = chat_parser.parse_folder_name(Path(input_path).name)
            agent_name = info.get("agent_name", "Unknown")
            customer_name = info.get("customer_name", Path(input_path).name)
            images = chat_parser.find_images(input_path)

            TASK_STATUS[session_id]["message"] = f"转录 {len(images)} 张截图..."

            result = chat_analyzer.analyze_conversation_images(images, customer_name, agent_name)
            result["_notes"] = info.get("notes", "")
            results = [result]
            parsed = {"metadata": {"source": "image_folder"}}
            agent_name = info.get("agent_name", "Unknown")

        else:
            TASK_STATUS[session_id] = {"status": "error", "message": f"未知类型: {input_type}"}
            return

        error_count = sum(1 for r in results if r.get("error"))
        success_count = len(results) - error_count
        s_count = sum(1 for r in results if r.get("customer_level") == "S")
        a_count = sum(1 for r in results if r.get("customer_level") == "A")
        b_large = sum(1 for r in results if r.get("is_b_large"))
        metadata = parsed.get("metadata", {})

        # Save all reports with consistent filename
        with _task_lock:
            TASK_STATUS[session_id]["message"] = "生成总结中..."

        summary = chat_analyzer.generate_summary(results, agent_name)

        # Use a single timestamp for all files
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        base_name = f"{agent_name}_{timestamp}"
        os.makedirs(str(REPORT_DIR), exist_ok=True)

        html_content = chat_reporter.generate_html(results, summary, agent_name, metadata)
        html_path = str(REPORT_DIR / f"{base_name}.html")
        with open(html_path, "w", encoding="utf-8") as f:
            f.write(html_content)

        json_path = str(REPORT_DIR / f"{base_name}.json")
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(results, f, ensure_ascii=False, indent=2)

        csv_path = str(REPORT_DIR / f"{base_name}.csv")
        with open(csv_path, "w", encoding="utf-8-sig") as f:
            f.write(chat_reporter.generate_csv(results))

        key_html = chat_reporter.generate_key_followup_html(results, agent_name)
        key_path = str(REPORT_DIR / f"{base_name}_重点跟进.html")
        with open(key_path, "w", encoding="utf-8") as f:
            f.write(key_html)

        # Save to history index
        _save_history(base_name, {
            "base_name": base_name,
            "agent_name": agent_name,
            "original_files": original_files,
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "total_customers": len(results),
            "s_count": s_count,
            "a_count": a_count,
            "b_large": b_large,
            "input_type": input_type,
        })

        with _task_lock:
            TASK_STATUS[session_id] = {
                "status": "done",
                "progress": 100,
                "message": "分析完成",
                "agent_name": agent_name,
                "total": len(results),
                "s_count": s_count,
                "a_count": a_count,
                "b_large": b_large,
                "html_path": f"{base_name}.html",
                "json_path": f"{base_name}.json",
                "csv_path": f"{base_name}.csv",
                "key_path": f"{base_name}_重点跟进.html",
                "html_url": f"/reports/{base_name}.html",
                "csv_url": f"/reports/{base_name}.csv",
                "key_url": f"/reports/{base_name}_重点跟进.html",
                "results": results,
            }

    except Exception as e:
        with _task_lock:
            TASK_STATUS[session_id] = {"status": "error", "message": str(e)}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        print(f"[{datetime.now().strftime('%H:%M:%S')}] {args[0]}")

    def _send_json(self, data, status=200):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", len(body))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _send_html(self, content, status=200):
        body = content.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", len(body))
        self.end_headers()
        self.wfile.write(body)

    def _send_file(self, path, content_type, download=True):
        if not os.path.exists(path):
            self.send_error(404)
            return
        with open(path, "rb") as f:
            data = f.read()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", len(data))
        if download:
            filename = os.path.basename(path)
            encoded = quote(filename, safe="")
            self.send_header("Content-Disposition",
                           f"attachment; filename*=UTF-8''{encoded}")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path

        if path == "/" or path == "/index.html":
            self._send_html(get_frontend_html())
        elif path == "/api/restart":
            self._send_json({"status": "restarting"})

            def do_restart():
                time.sleep(1)
                subprocess.Popen(
                    [sys.executable, str(ROOT / "server.py")],
                    cwd=str(ROOT), start_new_session=True,
                )
                os._exit(0)

            threading.Thread(target=do_restart, daemon=True).start()
        elif path == "/api/status":
            session_id = parsed.query.split("=")[-1] if "=" in parsed.query else ""
            with _task_lock:
                status = TASK_STATUS.get(session_id, {"status": "not_found"})
            self._send_json(status)
        elif path.startswith("/reports/"):
            filename = os.path.basename(unquote(path))
            filepath = REPORT_DIR / filename
            params = parse_qs(parsed.query)
            force_download = "download" in params
            if not filepath.exists():
                self.send_error(404)
            elif filename.endswith(".html"):
                self._send_file(str(filepath), "text/html; charset=utf-8", download=force_download)
            elif filename.endswith(".csv"):
                self._send_file(str(filepath), "text/csv; charset=utf-8", download=True)
            elif filename.endswith(".json"):
                self._send_file(str(filepath), "application/json", download=True)
            else:
                self.send_error(404)
        elif path == "/api/reports":
            history_index = {h["base_name"]: h for h in _load_history()}
            reports = []
            for f in sorted(REPORT_DIR.glob("*.html"), key=os.path.getmtime, reverse=True):
                if "_重点跟进" in f.name:
                    continue
                base = f.name.replace('.html', '')
                csv_name = f.name.replace('.html', '.csv')
                csv_exists = (REPORT_DIR / csv_name).exists()
                key_name = f.name.replace('.html', '_重点跟进.html')
                key_exists = (REPORT_DIR / key_name).exists()
                hist = history_index.get(base, {})
                reports.append({
                    "name": f.name,
                    "base_name": base,
                    "url": f"/reports/{f.name}",
                    "csv_url": f"/reports/{csv_name}" if csv_exists else None,
                    "key_url": f"/reports/{key_name}" if key_exists else None,
                    "time": datetime.fromtimestamp(os.path.getmtime(f)).strftime("%Y-%m-%d %H:%M"),
                    "original_files": hist.get("original_files", []),
                    "agent_name": hist.get("agent_name", ""),
                    "total_customers": hist.get("total_customers", 0),
                    "s_count": hist.get("s_count", 0),
                    "a_count": hist.get("a_count", 0),
                })
            self._send_json(reports[:50])
        elif path == "/api/dashboard/scores":
            params = parse_qs(parsed.query)
            days = int(params.get("days", [30])[0])
            scores = chat_scorer.get_agent_scores(days)
            self._send_json(scores)
        elif path == "/api/dashboard/key-customers":
            params = parse_qs(parsed.query)
            days = int(params.get("days", [30])[0])
            agent_filter = params.get("agent", [""])[0]
            customers = chat_scorer.get_key_customers(days)
            if agent_filter:
                customers = [c for c in customers if c.get("agent_name") == agent_filter]
            self._send_json(customers)
        elif path == "/api/dashboard/pending-followup":
            params = parse_qs(parsed.query)
            days = int(params.get("days", [1])[0])
            agent_filter = params.get("agent", [""])[0]
            pending = chat_scorer.get_pending_followups(days)
            if agent_filter:
                pending = [c for c in pending if c.get("agent_name") == agent_filter]
            self._send_json(pending)
        elif path == "/api/dashboard/weekly-summary":
            params = parse_qs(parsed.query)
            days = int(params.get("days", [7])[0])
            summary = chat_scorer.get_weekly_summary(days)
            self._send_json(summary)
        elif path == "/api/dashboard/combined-diagnosis":
            params = parse_qs(parsed.query)
            days = int(params.get("days", [7])[0])
            diagnosis = chat_scorer.combined_diagnosis(days, _LATEST_DEALS)
            self._send_json(diagnosis)
        else:
            self.send_error(404)

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path

        if path == "/api/upload":
            self._handle_upload()
        elif path == "/api/upload-deals":
            self._handle_deal_upload()
        elif path == "/api/analyze":
            self._handle_analyze()
        else:
            self.send_error(404)

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def _parse_multipart(self, body: bytes, boundary: str) -> list:
        """Parse multipart/form-data body, return list of (filename, data) tuples."""
        results = []
        boundary_bytes = ("--" + boundary).encode()
        end_boundary = ("--" + boundary + "--").encode()

        # Split by boundary
        parts = body.split(boundary_bytes)
        for part in parts[1:]:  # skip preamble
            if part.startswith(b"--"):
                break  # end boundary
            if part.startswith(b"\r\n"):
                part = part[2:]

            # Split headers from body
            if b"\r\n\r\n" in part:
                header_bytes, file_data = part.split(b"\r\n\r\n", 1)
                headers_text = header_bytes.decode("utf-8", errors="replace")

                # Extract filename
                m = re.search(r'filename="([^"]*)"', headers_text)
                if not m:
                    m = re.search(r"filename=(\S+)", headers_text)
                if not m:
                    continue
                filename = m.group(1)

                # Strip trailing \r\n and boundary artifacts from data
                if file_data.endswith(b"\r\n"):
                    file_data = file_data[:-2]

                results.append((filename, file_data))
        return results

    def _handle_upload(self):
        content_type = self.headers.get("Content-Type", "")
        if "multipart/form-data" not in content_type:
            self._send_json({"error": "需要 multipart/form-data"}, 400)
            return

        boundary = content_type.split("boundary=")[-1].strip()
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)

        parts = self._parse_multipart(body, boundary)
        if not parts:
            self._send_json({"error": "未解析到文件"}, 400)
            return

        session_id = datetime.now().strftime("%Y%m%d_%H%M%S")
        session_dir = UPLOAD_DIR / session_id
        session_dir.mkdir(exist_ok=True)

        files_saved = []
        input_type = None

        for filename, file_data in parts:
            # Keep uploads inside the per-session directory even when a client
            # submits a path-like filename.
            safe_name = Path(filename).name or "upload.bin"
            save_path = session_dir / safe_name
            with open(save_path, "wb") as f:
                f.write(file_data)
            files_saved.append(str(save_path))

            if filename.endswith(".json"):
                input_type = "json"
            elif filename.endswith(".zip"):
                input_type = "zip"

        if not files_saved:
            self._send_json({"error": "未接收到文件"}, 400)
            return

        if input_type == "zip":
            extract_dir = str(session_dir / "extracted")
            os.makedirs(extract_dir, exist_ok=True)
            with zipfile.ZipFile(files_saved[0], "r") as zf:
                root = Path(extract_dir).resolve()
                for member in zf.infolist():
                    target = (root / member.filename).resolve()
                    if target != root and root not in target.parents:
                        self._send_json({"error": "ZIP 文件包含非法路径"}, 400)
                        shutil.rmtree(session_dir, ignore_errors=True)
                        return
                zf.extractall(root)
            # Check if extracted is a single folder
            items = os.listdir(extract_dir)
            if len(items) == 1 and os.path.isdir(os.path.join(extract_dir, items[0])):
                extract_dir = os.path.join(extract_dir, items[0])
            # Check for images
            has_images = len(chat_parser.find_images(extract_dir)) > 0
            subdirs = [d for d in Path(extract_dir).iterdir() if d.is_dir()]
            if subdirs:
                input_type = "images"
                input_path = extract_dir
            elif has_images:
                input_type = "image_folder"
                input_path = extract_dir
            else:
                input_type = "unknown"
                input_path = extract_dir
        elif input_type == "json":
            input_path = files_saved[0]
        elif len(files_saved) > 0:
            # Check for images
            has_images = bool(chat_parser.find_images(str(session_dir)))
            if has_images:
                input_type = "image_folder"
                input_path = str(session_dir)
            else:
                input_type = "unknown"
                input_path = str(session_dir)

        if input_type and input_type != "unknown":
            original_names = [os.path.basename(f) for f in files_saved]
            t = threading.Thread(target=run_analysis,
                                 args=(session_id, input_path, input_type, original_names))
            t.daemon = True
            t.start()
            self._send_json({
                "session_id": session_id,
                "status": "started",
                "input_type": input_type,
                "files": [os.path.basename(f) for f in files_saved],
            })
        else:
            self._send_json({
                "session_id": session_id,
                "status": "unknown_format",
                "files": [os.path.basename(f) for f in files_saved],
                "message": "无法识别文件格式，请上传 JSON 文件或包含聊天截图的文件夹/zip",
            })

    def _handle_deal_upload(self):
        content_type = self.headers.get("Content-Type", "")
        if "multipart/form-data" not in content_type:
            self._send_json({"error": "需要 multipart/form-data"}, 400)
            return

        boundary = content_type.split("boundary=")[-1].strip()
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)
        parts = self._parse_multipart(body, boundary)

        if not parts:
            self._send_json({"error": "未解析到文件"}, 400)
            return

        import tempfile
        for filename, file_data in parts:
            if filename.endswith((".csv", ".tsv", ".txt")):
                tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".csv")
                tmp.write(file_data)
                tmp.close()
                global _LATEST_DEALS
                deals, headers = chat_scorer.parse_deal_csv(tmp.name)
                _LATEST_DEALS = deals
                os.unlink(tmp.name)
                from collections import Counter
                agent_counts = Counter(d.get("agent", "未知") for d in _LATEST_DEALS)
                self._send_json({
                    "status": "ok",
                    "deal_count": len(_LATEST_DEALS),
                    "csv_headers": headers,
                    "agent_distribution": dict(agent_counts),
                    "deals": _LATEST_DEALS,
                    "message": f"已解析 {len(_LATEST_DEALS)} 条成交记录",
                })
                return

        self._send_json({"error": "未找到CSV文件"}, 400)

    def _handle_analyze(self):
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length))
        action = body.get("action", "")

        if action == "list_reports":
            history_index = {h["base_name"]: h for h in _load_history()}
            reports = []
            for f in sorted(REPORT_DIR.glob("*.html"), key=os.path.getmtime, reverse=True):
                if "_重点跟进" in f.name:
                    continue
                base = f.name.replace('.html', '')
                csv_name = f.name.replace('.html', '.csv')
                csv_exists = (REPORT_DIR / csv_name).exists()
                key_name = f.name.replace('.html', '_重点跟进.html')
                key_exists = (REPORT_DIR / key_name).exists()
                hist = history_index.get(base, {})
                reports.append({
                    "name": f.name,
                    "base_name": base,
                    "html_url": f"/reports/{f.name}",
                    "csv_url": f"/reports/{csv_name}" if csv_exists else None,
                    "key_url": f"/reports/{key_name}" if key_exists else None,
                    "time": datetime.fromtimestamp(os.path.getmtime(f)).strftime("%Y-%m-%d %H:%M"),
                    "original_files": hist.get("original_files", []),
                    "agent_name": hist.get("agent_name", ""),
                    "total_customers": hist.get("total_customers", 0),
                    "s_count": hist.get("s_count", 0),
                    "a_count": hist.get("a_count", 0),
                })
            self._send_json({"reports": reports})
        else:
            self._send_json({"error": "unknown action"}, 400)


def get_frontend_html():
    return r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>销售客服分析诊断系统</title>
<style>
*, *::before, *::after { margin: 0; padding: 0; box-sizing: border-box; }
body {
  font-family: -apple-system, BlinkMacSystemFont, "SF Pro Display", "PingFang SC", "Microsoft YaHei", sans-serif;
  background: #f3f4f6;
  color: #111827;
  min-height: 100vh;
  -webkit-font-smoothing: antialiased;
  display: flex;
}

/* ── Sidebar ──────────────────────────────── */
.sidebar {
  width: 240px;
  min-width: 240px;
  height: 100vh;
  position: sticky;
  top: 0;
  background: #fff;
  border-right: 1px solid #e5e7eb;
  display: flex;
  flex-direction: column;
  z-index: 50;
}
.sidebar-brand {
  padding: 24px 20px 20px;
  border-bottom: 1px solid #f3f4f6;
}
.sidebar-brand h1 {
  font-size: 16px;
  font-weight: 700;
  color: #111827;
  letter-spacing: -0.01em;
  line-height: 1.3;
}
.sidebar-brand .sub {
  font-size: 11px;
  color: #9ca3af;
  margin-top: 2px;
  font-weight: 500;
}
.sidebar-nav {
  flex: 1;
  padding: 12px 10px;
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.nav-item {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 10px 14px;
  border-radius: 8px;
  font-size: 14px;
  font-weight: 500;
  color: #4b5563;
  cursor: pointer;
  border: none;
  background: none;
  transition: all .15s ease;
  text-align: left;
  width: 100%;
}
.nav-item:hover { background: #f3f4f6; color: #111827; }
.nav-item.active { background: #eff6ff; color: #2563eb; }
.nav-item .nav-icon { font-size: 18px; width: 24px; text-align: center; flex-shrink: 0; }
.nav-item .nav-badge {
  margin-left: auto;
  background: #e85440;
  color: #fff;
  font-size: 10px;
  padding: 2px 7px;
  border-radius: 10px;
  font-weight: 600;
}
.sidebar-footer {
  padding: 16px 20px;
  border-top: 1px solid #f3f4f6;
  font-size: 11px;
  color: #9ca3af;
}
.restart-btn {
  width: 100%;
  padding: 8px 0;
  border: 1px solid #e5e7eb;
  border-radius: 8px;
  background: #fff;
  color: #6b7280;
  font-size: 12px;
  cursor: pointer;
  transition: all 0.2s;
}
.restart-btn:hover { background: #fef2f2; border-color: #fca5a5; color: #dc2626; }
.restart-btn:disabled { opacity: 0.6; cursor: not-allowed; }

/* ── Main Content ──────────────────────────── */
.main-content {
  flex: 1;
  min-width: 0;
  overflow-y: auto;
  height: 100vh;
}
.page-header {
  background: #fff;
  border-bottom: 1px solid #e5e7eb;
  padding: 20px 28px;
  position: sticky;
  top: 0;
  z-index: 30;
}
.page-header h2 {
  font-size: 20px;
  font-weight: 700;
  letter-spacing: -0.01em;
}
.page-header .breadcrumb {
  font-size: 12px;
  color: #9ca3af;
  margin-bottom: 4px;
}
.content-area { padding: 24px 28px 48px; max-width: 1100px; }

/* ── Stats Row ─────────────────────────────── */
.stats-row {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 16px;
  margin-bottom: 24px;
}
.stat-card {
  background: #fff;
  border-radius: 12px;
  padding: 20px 24px;
  border: 1px solid #e5e7eb;
  display: flex;
  align-items: center;
  gap: 16px;
}
.stat-card .stat-icon {
  width: 44px; height: 44px;
  border-radius: 10px;
  display: flex; align-items: center; justify-content: center;
  font-size: 20px;
  flex-shrink: 0;
}
.stat-card .stat-val { font-size: 26px; font-weight: 700; letter-spacing: -0.01em; }
.stat-card .stat-lbl { font-size: 12px; color: #6b7280; margin-top: 2px; }

/* ── Panel ─────────────────────────────────── */
.panel { display: none; }
.panel.active { display: block; }

/* ── Card ──────────────────────────────────── */
.card {
  background: #fff;
  border-radius: 12px;
  padding: 24px;
  margin-bottom: 20px;
  border: 1px solid #e5e7eb;
}
.card-header {
  display: flex; align-items: center; justify-content: space-between;
  margin-bottom: 20px; flex-wrap: wrap; gap: 12px;
}
.card-header h3 {
  font-size: 16px; font-weight: 700; color: #111827; letter-spacing: -0.01em;
  display: flex; align-items: center; gap: 8px;
}
.card-header h3 .dot {
  width: 8px; height: 8px; border-radius: 50%; flex-shrink: 0;
}

/* ── Time Filter Chips ─────────────────────── */
.time-filter { display: flex; gap: 4px; }
.time-chip {
  padding: 5px 14px; border-radius: 6px; border: 1px solid #d1d5db;
  background: #fff; font-size: 12px; cursor: pointer; color: #6b7280;
  font-weight: 500; transition: all .15s ease;
}
.time-chip.active { background: #111827; color: #fff; border-color: #111827; }
.time-chip:hover:not(.active) { border-color: #9ca3af; color: #111827; }

/* ── Filter Row ────────────────────────────── */
.filter-row { display: flex; gap: 6px; flex-wrap: wrap; }
.chip {
  padding: 4px 12px; border-radius: 6px; border: 1px solid #d1d5db;
  background: #fff; font-size: 12px; cursor: pointer; font-weight: 500;
  color: #6b7280; transition: all .15s ease;
}
.chip.active { background: #111827; color: #fff; border-color: #111827; }
.chip:hover:not(.active) { border-color: #9ca3af; }

/* ── Score Cards Grid ──────────────────────── */
.score-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 14px; }
.agent-score-card {
  background: #fff; border-radius: 12px; padding: 20px;
  border: 1px solid #e5e7eb; transition: box-shadow .2s ease;
}
.agent-score-card:hover { box-shadow: 0 4px 12px rgba(0,0,0,0.06); }
.agent-score-card .agent-header {
  display: flex; align-items: center; justify-content: space-between; margin-bottom: 14px;
}
.agent-score-card .agent-name { font-size: 15px; font-weight: 700; }
.agent-score-card .agent-meta { font-size: 11px; color: #9ca3af; }
.agent-score-card .overall {
  text-align: center; margin: 12px 0; padding: 12px 0;
  border-top: 1px solid #f3f4f6; border-bottom: 1px solid #f3f4f6;
}
.agent-score-card .overall .num { font-size: 44px; font-weight: 800; line-height: 1; letter-spacing: -0.02em; }
.agent-score-card .overall .lbl { font-size: 10px; color: #9ca3af; margin-top: 4px; font-weight: 500; text-transform: uppercase; letter-spacing: 0.04em; }
.agent-score-card .dims { display: flex; flex-direction: column; gap: 4px; }
.agent-score-card .dim {
  display: flex; align-items: center; justify-content: space-between;
  padding: 5px 10px; background: #f9fafb; border-radius: 6px; font-size: 12px;
}
.agent-score-card .dim .dim-lbl { color: #6b7280; }
.agent-score-card .dim .dim-val { font-weight: 700; }
.agent-score-card .stats {
  display: flex; gap: 6px; margin-top: 10px; font-size: 10px; color: #6b7280; flex-wrap: wrap;
}
.agent-score-card .stats span { background: #f3f4f6; padding: 3px 8px; border-radius: 4px; font-weight: 500; }
.score-high { color: #059669; }
.score-mid { color: #d97706; }
.score-low { color: #dc2626; }

/* ── Tables ────────────────────────────────── */
.data-table { width: 100%; border-collapse: collapse; font-size: 13px; }
.data-table th, .data-table td { padding: 10px 14px; text-align: left; border-bottom: 1px solid #f3f4f6; }
.data-table th { background: #f9fafb; font-weight: 600; color: #6b7280; font-size: 11px; letter-spacing: 0.02em; text-transform: uppercase; }
.data-table tbody tr { transition: background .1s ease; }
.data-table tbody tr:hover { background: #f9fafb; }
.data-table .cust-name { font-weight: 600; color: #111827; }
.data-table .overview { max-width: 200px; color: #4b5563; font-size: 12px; line-height: 1.4; }

/* ── Badges ────────────────────────────────── */
.badge { display: inline-block; padding: 2px 8px; border-radius: 4px; font-size: 11px; font-weight: 600; color: #fff; }
.badge-S { background: #dc2626; }
.badge-A { background: #d97706; }
.badge-B { background: #6b7280; }
.badge-C { background: #9ca3af; }
.badge-b { background: #2563eb; }
.badge-blarge { background: #7c3aed; }
.badge-home { background: #059669; }

/* ── Empty State ───────────────────────────── */
.empty-state { text-align: center; padding: 48px 24px; color: #9ca3af; font-size: 14px; }
.empty-state .icon { font-size: 40px; margin-bottom: 10px; opacity: 0.5; }

/* ── Upload Zone ───────────────────────────── */
.upload-zone {
  border: 2px dashed #d1d5db; border-radius: 12px; padding: 40px 24px;
  text-align: center; cursor: pointer; transition: all .2s ease; background: #fafafa;
}
.upload-zone:hover, .upload-zone.dragover { border-color: #2563eb; background: #eff6ff; border-style: solid; }
.upload-zone .text { font-size: 14px; color: #6b7280; font-weight: 500; }
.upload-zone .hint { font-size: 12px; color: #9ca3af; margin-top: 6px; }

/* ── File List ─────────────────────────────── */
.file-list { margin-top: 14px; }
.file-item {
  display: flex; align-items: center; justify-content: space-between;
  padding: 8px 14px; background: #f9fafb; border-radius: 8px; margin-bottom: 4px; font-size: 13px;
}
.file-item .name { font-weight: 500; }
.file-item .size { color: #9ca3af; font-size: 11px; }
.file-item .remove { cursor: pointer; color: #dc2626; font-size: 16px; padding: 0 4px; }

/* ── Buttons ───────────────────────────────── */
.btn {
  display: inline-block; padding: 9px 18px; border-radius: 8px; font-size: 13px;
  font-weight: 600; cursor: pointer; border: none; transition: all .15s ease;
  text-decoration: none; letter-spacing: -0.01em;
}
.btn-primary { background: #2563eb; color: #fff; }
.btn-primary:hover { background: #1d4ed8; }
.btn-primary:disabled { background: #d1d5db; cursor: not-allowed; }
.btn-secondary { background: #f3f4f6; color: #111827; }
.btn-secondary:hover { background: #e5e7eb; }

/* ── Progress ──────────────────────────────── */
.progress { margin-top: 14px; display: none; }
.progress.active { display: block; }
.progress-bar { height: 4px; background: #e5e7eb; border-radius: 2px; overflow: hidden; margin-top: 6px; }
.progress-bar .fill { height: 100%; background: #2563eb; border-radius: 2px; width: 0%; transition: width .3s ease; }
.progress-message { font-size: 12px; color: #6b7280; margin-top: 4px; }

/* ── Result Area ───────────────────────────── */
.result-stats { display: grid; grid-template-columns: repeat(auto-fit, minmax(80px, 1fr)); gap: 8px; margin-bottom: 18px; }
.result-stat { background: #f9fafb; padding: 12px; border-radius: 8px; text-align: center; }
.result-stat .num { font-size: 22px; font-weight: 700; }
.result-stat .lbl { font-size: 10px; color: #6b7280; margin-top: 2px; }
.result-stat.s .num { color: #dc2626; }
.result-stat.a .num { color: #d97706; }
.result-links { display: flex; gap: 8px; flex-wrap: wrap; margin-top: 16px; }
.result-links .btn { font-size: 12px; padding: 7px 14px; }

.result-table-wrap { overflow-x: auto; }
.result-table-wrap table { width: 100%; border-collapse: collapse; font-size: 13px; }
.result-table-wrap th, .result-table-wrap td { padding: 8px 12px; text-align: left; border-bottom: 1px solid #f3f4f6; }
.result-table-wrap th { background: #f9fafb; font-weight: 600; color: #6b7280; font-size: 11px; white-space: nowrap; }
.result-table-wrap tr:hover { background: #fafafa; }
.actions-col { max-width: 240px; font-size: 12px; line-height: 1.4; }
.issues-col { max-width: 240px; font-size: 12px; line-height: 1.4; }
.ellipsis { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; max-width: 130px; }

/* ── History ───────────────────────────────── */
.history-item {
  display: flex; justify-content: space-between; align-items: center;
  padding: 12px 0; border-bottom: 1px solid #f3f4f6; font-size: 14px;
}
.history-item:last-child { border-bottom: none; }
.history-item .time { color: #9ca3af; font-size: 12px; }

/* ── Responsive ────────────────────────────── */
@media (max-width: 768px) {
  .sidebar { width: 64px; min-width: 64px; }
  .sidebar-brand h1 { font-size: 0; }
  .sidebar-brand .sub { display: none; }
  .sidebar-brand { padding: 16px 10px; }
  .nav-item { justify-content: center; padding: 12px; font-size: 0; gap: 0; }
  .nav-item .nav-icon { font-size: 20px; }
  .nav-item .nav-badge { display: none; }
  .content-area { padding: 16px 12px; }
  .stats-row { grid-template-columns: repeat(2, 1fr); }
  .score-grid { grid-template-columns: 1fr; }
  .page-header { padding: 14px 16px; }
  .page-header h2 { font-size: 17px; }
}
</style>
</head>
<body>

<!-- ── Sidebar ──────────────────────────────── -->
<aside class="sidebar">
  <div class="sidebar-brand">
    <h1>销售客服分析诊断系统</h1>
    <div class="sub">Sales Chat Analytics</div>
  </div>
  <nav class="sidebar-nav">
    <button class="nav-item active" onclick="switchPanel('scores')" data-panel="scores">
      <span class="nav-icon">&#x1f4ca;</span>客服评分
    </button>
    <button class="nav-item" onclick="switchPanel('customers')" data-panel="customers">
      <span class="nav-icon">&#x1f465;</span>重点客户
    </button>
    <button class="nav-item" onclick="switchPanel('upload')" data-panel="upload">
      <span class="nav-icon">&#x1f4e4;</span>上传分析
    </button>
    <button class="nav-item" onclick="switchPanel('weekly')" data-panel="weekly">
      <span class="nav-icon">&#x1f4c5;</span>周报诊断
    </button>
  </nav>
  <div class="sidebar-footer">
    <button class="restart-btn" onclick="restartServer(this)">&#x1f504; 重启服务</button>
    <div style="margin-top:8px;">GD Li &copy; 2026</div>
  </div>
</aside>

<!-- ── Main Content ─────────────────────────── -->
<main class="main-content">
  <div class="page-header">
    <div class="breadcrumb" id="breadcrumb">仪表盘</div>
    <h2 id="page-title">客服评分</h2>
  </div>
  <div class="content-area">

    <!-- ═══ PANEL: 客服评分 ═══ -->
    <div class="panel active" id="panel-scores">
      <div class="card">
        <div class="card-header">
          <h3><span class="dot" style="background:#2563eb;"></span>近期客服评分</h3>
          <div class="time-filter" id="score-time-filter">
            <span class="time-chip" data-days="7">近7天</span>
            <span class="time-chip" data-days="14">近14天</span>
            <span class="time-chip active" data-days="30">近30天</span>
            <span class="time-chip" data-days="90">近90天</span>
          </div>
        </div>
        <div class="score-grid" id="score-grid">
          <div class="empty-state"><div class="icon">&#x1f4ca;</div>加载中...</div>
        </div>
      </div>
    </div>

    <!-- ═══ PANEL: 重点客户 ═══ -->
    <div class="panel" id="panel-customers">
      <!-- Pending Follow-up -->
      <div class="card" style="border-left: 3px solid #dc2626;">
        <div class="card-header">
          <h3><span class="dot" style="background:#dc2626;"></span>&#x1f514; 昨天待跟进客户
            <span style="font-size:12px;font-weight:500;color:#dc2626;">需要立即行动</span>
          </h3>
          <div style="display:flex;gap:10px;flex-wrap:wrap;">
            <div class="time-filter" id="pending-time-filter">
              <span class="time-chip active" data-days="1">昨天</span>
              <span class="time-chip" data-days="2">近2天</span>
              <span class="time-chip" data-days="3">近3天</span>
            </div>
          </div>
        </div>
        <div class="filter-row" id="pending-agent-filter" style="margin-bottom:14px;">
          <span class="chip active" data-agent="" onclick="filterPendingAgent('', this)">全部</span>
          <span class="chip" data-agent="客服甲" onclick="filterPendingAgent('客服甲', this)">客服甲</span>
          <span class="chip" data-agent="客服乙" onclick="filterPendingAgent('客服乙', this)">客服乙</span>
          <span class="chip" data-agent="客服丙" onclick="filterPendingAgent('客服丙', this)">客服丙</span>
          <span class="chip" data-agent="客服丁" onclick="filterPendingAgent('客服丁', this)">客服丁</span>
        </div>
        <div style="overflow-x:auto;">
          <table class="data-table" id="pending-table">
            <thead>
              <tr><th>客户ID</th><th>所属客服</th><th>评级</th><th>客户类型</th><th>销售阶段</th><th>消息数</th><th>概述</th><th>分析时间</th></tr>
            </thead>
            <tbody id="pending-tbody"></tbody>
          </table>
        </div>
        <div class="empty-state" id="pending-empty" style="display:none;">
          <div class="icon">&#x2705;</div>暂无待跟进客户，干得漂亮
        </div>
      </div>

      <!-- All Key Customers -->
      <div class="card">
        <div class="card-header">
          <h3><span class="dot" style="background:#059669;"></span>近期重点客户跟进</h3>
          <div class="time-filter" id="cust-time-filter">
            <span class="time-chip" data-days="7">近7天</span>
            <span class="time-chip" data-days="14">近14天</span>
            <span class="time-chip active" data-days="30">近30天</span>
            <span class="time-chip" data-days="90">近90天</span>
          </div>
        </div>
        <div class="filter-row" id="cust-agent-filter" style="margin-bottom:14px;">
          <span class="chip active" data-agent="" onclick="filterCustAgent('', this)">全部</span>
          <span class="chip" data-agent="客服甲" onclick="filterCustAgent('客服甲', this)">客服甲</span>
          <span class="chip" data-agent="客服乙" onclick="filterCustAgent('客服乙', this)">客服乙</span>
          <span class="chip" data-agent="客服丙" onclick="filterCustAgent('客服丙', this)">客服丙</span>
          <span class="chip" data-agent="客服丁" onclick="filterCustAgent('客服丁', this)">客服丁</span>
        </div>
        <div style="overflow-x:auto;">
          <table class="data-table" id="customer-table">
            <thead>
              <tr><th>客户ID</th><th>所属客服</th><th>评级</th><th>客户类型</th><th>销售阶段</th><th>概述</th><th>分析时间</th></tr>
            </thead>
            <tbody id="customer-tbody"></tbody>
          </table>
        </div>
        <div class="empty-state" id="cust-empty" style="display:none;">
          <div class="icon">&#x1f389;</div>暂无需要重点跟进的客户
        </div>
      </div>
    </div>

    <!-- ═══ PANEL: 上传分析 ═══ -->
    <div class="panel" id="panel-upload">
      <div class="card" id="upload-card">
        <div class="card-header">
          <h3><span class="dot" style="background:#d97706;"></span>上传聊天记录</h3>
        </div>
        <div class="upload-zone" id="drop-zone">
          <div class="text">拖拽文件到此处或点击上传</div>
          <div class="hint">支持 .json / .zip / 图片文件 &middot; JSON: 1688插件导出 &middot; ZIP: 聊天截图文件夹</div>
        </div>
        <input type="file" id="file-input" multiple accept=".json,.zip,.png,.jpg,.jpeg" style="display:none">
        <div class="file-list" id="file-list"></div>
        <div style="margin-top: 14px; display: flex; align-items: center; gap: 12px;">
          <button class="btn btn-primary" id="btn-upload" disabled>开始分析</button>
          <span style="font-size:12px;color:#9ca3af;" id="upload-hint"></span>
        </div>
        <div class="progress" id="progress">
          <div class="progress-bar"><div class="fill" id="progress-fill"></div></div>
          <div class="progress-message" id="progress-msg"></div>
        </div>
      </div>

      <div class="card" id="result-card" style="display:none;">
        <div class="card-header">
          <h3><span class="dot" style="background:#059669;"></span>分析结果</h3>
        </div>
        <div class="result-stats" id="result-stats"></div>
        <div class="result-links" id="result-links"></div>
        <div style="margin-top: 18px;">
          <div class="filter-row" id="filter-row" style="margin-bottom:12px;">
            <span class="chip active" onclick="filterTable('all', this)">全部</span>
            <span class="chip" onclick="filterTable('S', this)" style="color:#dc2626;">S级</span>
            <span class="chip" onclick="filterTable('A', this)" style="color:#d97706;">A级</span>
            <span class="chip" onclick="filterTable('B', this)" style="color:#6b7280;">B级</span>
            <span class="chip" onclick="filterTable('C', this)" style="color:#9ca3af;">C级</span>
            <span class="chip" onclick="filterTable('B端', this)" style="color:#2563eb;">B端</span>
          </div>
          <div class="result-table-wrap">
            <table id="result-table"><thead></thead><tbody></tbody></table>
          </div>
        </div>
      </div>

      <!-- History -->
      <div class="card" id="history-card">
        <div class="card-header">
          <h3><span class="dot" style="background:#7c3aed;"></span>历史分析记录</h3>
        </div>
        <div id="history-list">加载中...</div>
      </div>
    </div>

    <!-- ═══ PANEL: 周报诊断 ═══ -->
    <div class="panel" id="panel-weekly">
      <!-- Card 1: Analysis Results Summary -->
      <div class="card" id="analysis-summary-card">
        <div class="card-header">
          <h3><span class="dot" style="background:#2563eb;"></span>已分析结果汇总</h3>
          <span style="font-size:12px;color:#9ca3af;" id="analysis-summary-time"></span>
        </div>
        <div class="stats-row" id="analysis-stats-row">
          <div class="stat-card">
            <div class="stat-icon" style="background:#eff6ff;color:#2563eb;">&#x1f4ca;</div>
            <div><div class="stat-val" id="stat-total-inquiries">-</div><div class="stat-lbl">总询盘数</div></div>
          </div>
          <div class="stat-card">
            <div class="stat-icon" style="background:#fef2f2;color:#dc2626;">&#x1f3af;</div>
            <div><div class="stat-val" id="stat-total-sa">-</div><div class="stat-lbl">S+A 高意向</div></div>
          </div>
          <div class="stat-card">
            <div class="stat-icon" style="background:#fefce8;color:#d97706;">&#x2b50;</div>
            <div><div class="stat-val" id="stat-b-large">-</div><div class="stat-lbl">B端大客户</div></div>
          </div>
          <div class="stat-card">
            <div class="stat-icon" style="background:#ecfdf5;color:#059669;">&#x2705;</div>
            <div><div class="stat-val" id="stat-ordered">-</div><div class="stat-lbl">已成交</div></div>
          </div>
        </div>
        <div class="score-grid" id="analysis-agent-grid">
          <div class="empty-state"><div class="icon">&#x1f4ca;</div>加载中...</div>
        </div>
      </div>

      <!-- Card 2: Weekly Inquiry Summary with time filter -->
      <div class="card">
        <div class="card-header">
          <h3><span class="dot" style="background:#7c3aed;"></span>近一周询盘概要</h3>
          <div class="time-filter" id="weekly-time-filter">
            <span class="time-chip active" data-days="7">近7天</span>
            <span class="time-chip" data-days="14">近14天</span>
            <span class="time-chip" data-days="30">近30天</span>
          </div>
        </div>
        <div id="weekly-summary-text" style="background:#f9fafb;border-radius:8px;padding:14px 18px;margin-bottom:18px;font-size:13px;line-height:1.7;color:#4b5563;display:none;"></div>
        <div id="weekly-summary-grid">
          <div class="empty-state"><div class="icon">&#x1f4ca;</div>加载中...</div>
        </div>
      </div>

      <!-- Card 3: Deal Upload -->
      <div class="card">
        <div class="card-header">
          <h3><span class="dot" style="background:#059669;"></span>上传成交表
            <span style="font-size:12px;font-weight:500;color:#6b7280;">CSV / TSV 格式</span>
          </h3>
        </div>
        <div style="display:flex;gap:16px;align-items:flex-start;flex-wrap:wrap;">
          <div style="flex:1;min-width:240px;">
            <input type="file" id="deal-file-input" accept=".csv,.tsv,.txt" style="display:none">
            <div class="upload-zone" id="deal-drop-zone" style="padding:24px 20px;">
              <div class="text">拖拽成交表到此处</div>
              <div class="hint">列: 日期 / 销售客服 / 旺旺名称ID / 产品 / 货品总价</div>
            </div>
            <div id="deal-file-info" style="margin-top:8px;font-size:13px;color:#6b7280;display:none;"></div>
          </div>
          <div style="flex-shrink:0;display:flex;flex-direction:column;gap:6px;justify-content:center;">
            <button class="btn btn-primary" id="btn-upload-deal" disabled>上传并解析</button>
            <span style="font-size:12px;color:#9ca3af;" id="deal-status"></span>
          </div>
        </div>
      </div>

      <!-- Card 4: Combined Diagnosis -->
      <div class="card" id="diagnosis-card" style="display:none;">
        <div class="card-header">
          <h3><span class="dot" style="background:#d97706;"></span>综合诊断结果</h3>
        </div>
        <div id="diagnosis-overview" style="margin-bottom:18px;"></div>

        <h4 style="font-size:15px;font-weight:600;margin-bottom:10px;color:#111827;">客服对比视图</h4>
        <div style="overflow-x:auto;">
          <table class="data-table" id="agent-compare-table">
            <thead>
              <tr><th>客服</th><th>询盘总数</th><th>S+A数</th><th>S+A率</th><th>询盘成交</th><th>成交表笔数</th><th>成交表金额</th><th>核心问题</th></tr>
            </thead>
            <tbody id="agent-compare-tbody"></tbody>
          </table>
        </div>

        <h4 style="font-size:15px;font-weight:600;margin:20px 0 10px;color:#dc2626;">&#x26a0;&#xfe0f; 错失机会</h4>
        <p style="font-size:12px;color:#6b7280;margin-bottom:10px;">高意向但未出现在成交表中的客户</p>
        <div style="overflow-x:auto;">
          <table class="data-table" id="missed-table">
            <thead>
              <tr><th>客户</th><th>客服</th><th>评级</th><th>类型</th><th>产品</th><th>阶段</th></tr>
            </thead>
            <tbody id="missed-tbody"></tbody>
          </table>
        </div>
        <div class="empty-state" id="missed-empty" style="display:none;">
          <div class="icon">&#x2705;</div>所有高意向客户均已成交
        </div>
      </div>
    </div>

  </div>
</main>

<script>
(function() {
var files = [];
var currentResults = [];

var PAGE_TITLES = {
  scores: '客服评分',
  customers: '重点客户跟进',
  upload: '上传分析',
  weekly: '周报诊断'
};
var BREADCRUMB = { scores: '仪表盘', customers: '客户管理', upload: '分析工具', weekly: '报表中心' };

window.switchPanel = function(name) {
  document.querySelectorAll('.nav-item').forEach(function(t) { t.classList.remove('active'); });
  document.querySelectorAll('.panel').forEach(function(p) { p.classList.remove('active'); });
  var navItem = document.querySelector('.nav-item[data-panel="' + name + '"]');
  if (navItem) navItem.classList.add('active');
  document.getElementById('panel-' + name).classList.add('active');
  document.getElementById('page-title').textContent = PAGE_TITLES[name] || '';
  document.getElementById('breadcrumb').textContent = BREADCRUMB[name] || '';
  if (name === 'scores') loadScores();
  if (name === 'customers') { loadPending(); loadCustomers(); }
  if (name === 'upload') loadHistory();
  if (name === 'weekly') loadWeeklySummary();
};

function initTimeFilter(containerId, callback) {
  document.getElementById(containerId).addEventListener('click', function(e) {
    if (e.target.classList.contains('time-chip')) {
      this.querySelectorAll('.time-chip').forEach(function(c) { c.classList.remove('active'); });
      e.target.classList.add('active');
      callback(parseInt(e.target.dataset.days));
    }
  });
}

function scoreColor(v) {
  if (v >= 80) return 'score-high';
  if (v >= 60) return 'score-mid';
  return 'score-low';
}

function loadScores() {
  var days = document.querySelector('#score-time-filter .time-chip.active').dataset.days || '30';
  fetch('/api/dashboard/scores?days=' + days)
    .then(function(r) { return r.json(); })
    .then(function(data) {
      var grid = document.getElementById('score-grid');
      if (!data.length) {
        grid.innerHTML = '<div class="empty-state"><div class="icon">&#x1f4ca;</div>暂无评分数据<br><small>上传聊天记录分析后自动生成评分</small></div>';
        return;
      }
      grid.innerHTML = data.map(function(a) {
        var cls = scoreColor(a.overall);
        return '<div class="agent-score-card">' +
          '<div class="agent-header">' +
            '<div class="agent-name">' + esc(a.agent_name) + '</div>' +
            '<div class="agent-meta">' + a.batch_count + '批次 &middot; ' + a.latest_time + '</div>' +
          '</div>' +
          '<div class="overall">' +
            '<div class="num ' + cls + '">' + a.overall + '</div>' +
            '<div class="lbl">综合评分</div>' +
          '</div>' +
          '<div class="dims">' +
            '<div class="dim"><span class="dim-lbl">沟通力 (35%)</span><span class="dim-val ' + scoreColor(a.communication) + '">' + a.communication + '</span></div>' +
            '<div class="dim"><span class="dim-lbl">积极度 (35%)</span><span class="dim-val ' + scoreColor(a.proactiveness) + '">' + a.proactiveness + '</span></div>' +
            '<div class="dim"><span class="dim-lbl">转化率 (30%)</span><span class="dim-val ' + scoreColor(a.conversion) + '">' + a.conversion + '</span></div>' +
          '</div>' +
          '<div class="stats">' +
            '<span>S级: ' + (a.s_count||0) + '</span><span>A级: ' + (a.a_count||0) + '</span>' +
            '<span>S+A率: ' + (a.sa_rate||0) + '%</span><span>已成交: ' + (a.ordered_count||0) + '</span>' +
          '</div>' +
        '</div>';
      }).join('');
    });
}

initTimeFilter('score-time-filter', loadScores);

function loadCustomers() {
  var days = document.querySelector('#cust-time-filter .time-chip.active').dataset.days || '30';
  var agent = document.querySelector('#cust-agent-filter .chip.active').dataset.agent || '';
  var url = '/api/dashboard/key-customers?days=' + days;
  if (agent) url += '&agent=' + encodeURIComponent(agent);
  fetch(url)
    .then(function(r) { return r.json(); })
    .then(function(data) {
      var tbody = document.getElementById('customer-tbody');
      var empty = document.getElementById('cust-empty');
      if (!data.length) { tbody.innerHTML = ''; empty.style.display = ''; return; }
      empty.style.display = 'none';
      tbody.innerHTML = data.map(function(c) {
        var overview = (c.overview || '').substring(0, 20);
        var ctype = c.customer_type || '', isBl = c.is_b_large;
        var typeLabel = isBl ? 'B端大客户' : (ctype === 'B端' ? 'B端' : '家用');
        var typeBadge = isBl ? 'badge-blarge' : (ctype === 'B端' ? 'badge-b' : 'badge-home');
        return '<tr>' +
          '<td class="cust-name">' + esc(c.customer_name) + '</td>' +
          '<td>' + esc(c.agent_name) + '</td>' +
          '<td><span class="badge badge-' + c.customer_level + '">' + c.customer_level + '</span></td>' +
          '<td><span class="badge ' + typeBadge + '">' + esc(typeLabel) + '</span></td>' +
          '<td style="font-size:12px;">' + esc(c.sales_stage||'') + '</td>' +
          '<td class="overview">' + esc(overview) + '</td>' +
          '<td style="font-size:12px;color:#9ca3af;">' + (c.analysis_time||'') + '</td>' +
        '</tr>';
      }).join('');
    });
}

function filterCustAgent(agent, el) {
  document.querySelectorAll('#cust-agent-filter .chip').forEach(function(c) { c.classList.remove('active'); });
  el.classList.add('active');
  loadCustomers();
}
window.filterCustAgent = filterCustAgent;

initTimeFilter('cust-time-filter', loadCustomers);

function loadPending() {
  var days = document.querySelector('#pending-time-filter .time-chip.active').dataset.days || '1';
  var agent = document.querySelector('#pending-agent-filter .chip.active').dataset.agent || '';
  var url = '/api/dashboard/pending-followup?days=' + days;
  if (agent) url += '&agent=' + encodeURIComponent(agent);
  fetch(url)
    .then(function(r) { return r.json(); })
    .then(function(data) {
      var tbody = document.getElementById('pending-tbody');
      var empty = document.getElementById('pending-empty');
      if (!data.length) { tbody.innerHTML = ''; empty.style.display = ''; return; }
      empty.style.display = 'none';
      tbody.innerHTML = data.map(function(c) {
        var overview = (c.overview || '').substring(0, 20);
        var ctype = c.customer_type || '', isBl = c.is_b_large;
        var typeLabel = isBl ? 'B端大客户' : (ctype === 'B端' ? 'B端' : '家用');
        var typeBadge = isBl ? 'badge-blarge' : (ctype === 'B端' ? 'badge-b' : 'badge-home');
        return '<tr>' +
          '<td class="cust-name">' + esc(c.customer_name) + '</td>' +
          '<td>' + esc(c.agent_name) + '</td>' +
          '<td><span class="badge badge-' + c.customer_level + '">' + c.customer_level + '</span></td>' +
          '<td><span class="badge ' + typeBadge + '">' + esc(typeLabel) + '</span></td>' +
          '<td style="font-size:12px;">' + esc(c.sales_stage||'') + '</td>' +
          '<td style="font-size:12px;color:#6b7280;">' + (c.msg_count||0) + '条</td>' +
          '<td class="overview">' + esc(overview) + '</td>' +
          '<td style="font-size:12px;color:#9ca3af;">' + (c.analysis_time||'') + '</td>' +
        '</tr>';
      }).join('');
    });
}

function filterPendingAgent(agent, el) {
  document.querySelectorAll('#pending-agent-filter .chip').forEach(function(c) { c.classList.remove('active'); });
  el.classList.add('active');
  loadPending();
}
window.filterPendingAgent = filterPendingAgent;

initTimeFilter('pending-time-filter', loadPending);

// ── Upload Panel ──────────────────────────────
var dz = document.getElementById('drop-zone');
var fi = document.getElementById('file-input');
var fl = document.getElementById('file-list');
var btnUpload = document.getElementById('btn-upload');

dz.addEventListener('click', function() { fi.click(); });
dz.addEventListener('dragover', function(e) { e.preventDefault(); dz.classList.add('dragover'); });
dz.addEventListener('dragleave', function() { dz.classList.remove('dragover'); });
dz.addEventListener('drop', function(e) {
  e.preventDefault(); dz.classList.remove('dragover');
  addFiles(Array.from(e.dataTransfer.files));
});
fi.addEventListener('change', function() { addFiles(Array.from(fi.files)); });

function addFiles(newFiles) {
  newFiles.forEach(function(f) {
    if (!files.find(function(x) { return x.name === f.name; })) files.push(f);
  });
  renderFiles();
}

function renderFiles() {
  fl.innerHTML = files.map(function(f, i) {
    return '<div class="file-item"><span class="name">' + esc(f.name) + '</span><span class="size">' + fmt(f.size) + '</span><span class="remove" onclick="removeFile(' + i + ')">&times;</span></div>';
  }).join('');
  btnUpload.disabled = files.length === 0;
  document.getElementById('upload-hint').textContent = files.length ? files.length + ' 个文件' : '';
}

window.removeFile = function(i) { files.splice(i, 1); renderFiles(); };

btnUpload.addEventListener('click', function() {
  var formData = new FormData();
  files.forEach(function(f) { formData.append('files', f); });
  btnUpload.disabled = true; btnUpload.textContent = '上传中...';
  fetch('/api/upload', { method: 'POST', body: formData })
    .then(function(r) { return r.json(); })
    .then(function(data) {
      if (data.session_id) {
        document.getElementById('progress').classList.add('active');
        pollStatus(data.session_id);
      } else {
        alert('上传失败: ' + (data.message || data.error));
        btnUpload.disabled = false; btnUpload.textContent = '开始分析';
      }
    });
});

function pollStatus(sessionId) {
  fetch('/api/status?session=' + sessionId)
    .then(function(r) { return r.json(); })
    .then(function(data) {
      var fill = document.getElementById('progress-fill');
      var msg = document.getElementById('progress-msg');
      fill.style.width = (data.progress || 10) + '%';
      msg.textContent = data.message || '处理中...';
      if (data.status === 'done') {
        btnUpload.textContent = '重新分析'; btnUpload.disabled = false;
        document.getElementById('upload-hint').textContent = '';
        showResults(data);
      } else if (data.status === 'error') {
        msg.textContent = '错误: ' + data.message;
        btnUpload.textContent = '重试'; btnUpload.disabled = false;
      } else {
        setTimeout(function() { pollStatus(sessionId); }, 2000);
      }
    });
}

function showResults(data) {
  document.getElementById('result-card').style.display = '';
  document.getElementById('result-stats').innerHTML =
    '<div class="result-stat s"><div class="num">' + (data.s_count||0) + '</div><div class="lbl">S级 &middot; 紧急</div></div>' +
    '<div class="result-stat a"><div class="num">' + (data.a_count||0) + '</div><div class="lbl">A级 &middot; 重点</div></div>' +
    '<div class="result-stat"><div class="num">' + (data.total||0) + '</div><div class="lbl">总计</div></div>' +
    '<div class="result-stat"><div class="num">' + (data.b_large||0) + '</div><div class="lbl">B端大客户</div></div>';
  document.getElementById('result-links').innerHTML =
    '<a href="' + data.html_url + '" target="_blank" class="btn btn-primary">查看 HTML 报告</a>' +
    '<a href="' + data.html_url + '?download=1" class="btn btn-secondary">下载 HTML 报告</a>' +
    (data.csv_url ? '<a href="' + data.csv_url + '" class="btn btn-secondary">下载 CSV 表格</a>' : '') +
    (data.key_url ? '<a href="' + data.key_url + '" target="_blank" class="btn btn-secondary" style="background:#dc2626;color:#fff;">查看重点跟进表</a>' : '') +
    (data.key_url ? '<a href="' + data.key_url + '?download=1" class="btn btn-secondary" style="background:#dc2626;color:#fff;opacity:0.85;">下载重点跟进表</a>' : '');
  currentResults = data.results || [];
  loadHistory();
  renderTable(currentResults);
}

window.filterTable = function(level, el) {
  document.querySelectorAll('#filter-row .chip').forEach(function(c) { c.classList.remove('active'); });
  if (el) el.classList.add('active');
  document.querySelectorAll('#result-table tbody tr').forEach(function(tr) {
    if (level === 'all') tr.style.display = '';
    else if (level === 'B端') tr.style.display = tr.dataset.type === 'B端' ? '' : 'none';
    else tr.style.display = tr.dataset.level === level ? '' : 'none';
  });
};

function renderTable(results) {
  var headers = ['客户', '等级', '类型', '订单', '阶段', '产品', '需求量', '关键问题', '跟进建议'];
  var thead = document.getElementById('result-table').querySelector('thead');
  var tbody = document.getElementById('result-table').querySelector('tbody');
  thead.innerHTML = '<tr>' + headers.map(function(h) { return '<th>' + h + '</th>'; }).join('') + '</tr>';
  tbody.innerHTML = results.filter(function(r) { return !r.error; }).map(function(r) {
    var level = r.customer_level || 'C', ctype = r.customer_type || '', isBl = r.is_b_large;
    var stage = r.sales_stage || '';
    var key = r.key_info || {};
    var issues = (r.communication_issues || []).slice(0, 3).map(function(i) {
      return '[' + i.severity + '] ' + i.issue + (i.fix ? ' → ' + i.fix : '');
    }).join('<br>');
    var actions = (r.follow_up_actions || []).slice(0, 3).map(function(a) {
      if (typeof a === 'object') { return (a.priority||'') + ' ' + a.action; }
      return a;
    }).join('<br>');
    var typeLabel = isBl ? 'B端大客户' : (ctype === 'B端' ? 'B端' : '家用');
    var typeClass = isBl ? 'badge-blarge' : (ctype === 'B端' ? 'badge-b' : 'badge-home');
    var orderBadge = r.order_status === '已下单' ? '<span style="display:inline-block;padding:2px 6px;border-radius:4px;background:#059669;color:#fff;font-size:10px;font-weight:600;">已下单</span>' : '';
    return '<tr data-level="' + level + '" data-type="' + ctype + '">' +
      '<td><strong>' + esc(r.customer_name||'?') + '</strong></td>' +
      '<td><span class="badge badge-' + level + '">' + level + '</span></td>' +
      '<td><span class="badge ' + typeClass + '">' + esc(typeLabel) + '</span></td>' +
      '<td>' + orderBadge + '</td>' +
      '<td style="font-size:12px;">' + esc(stage) + '</td>' +
      '<td class="ellipsis">' + esc(key.product_interest||'') + '</td>' +
      '<td>' + esc(key.quantity||'') + '</td>' +
      '<td class="issues-col">' + issues + '</td>' +
      '<td class="actions-col">' + actions + '</td>' +
    '</tr>';
  }).join('');
}

function loadHistory() {
  fetch('/api/analyze', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({action: 'list_reports'}) })
  .then(function(r) { return r.json(); })
  .then(function(data) {
    var reports = data.reports || [];
    if (!reports.length) {
      document.getElementById('history-list').innerHTML = '<div style="padding:32px;text-align:center;color:#9ca3af;">暂无历史分析记录</div>';
      return;
    }
    document.getElementById('history-list').innerHTML = reports.map(function(r) {
      var filesHtml = (r.original_files||[]).length ? '<div style="font-size:11px;color:#9ca3af;margin-top:2px;">来源: ' + esc(r.original_files.join(', ')) + '</div>' : '';
      var statsHtml = r.total_customers ? '<span style="font-size:11px;color:#6b7280;">' + r.agent_name + '</span>' +
        '<span style="font-size:11px;color:#9ca3af;margin-left:8px;">' + r.total_customers + '客户</span>' +
        '<span style="font-size:11px;color:#dc2626;margin-left:6px;">S:' + (r.s_count||0) + '</span>' +
        '<span style="font-size:11px;color:#d97706;margin-left:4px;">A:' + (r.a_count||0) + '</span>' : '';
      return '<div class="history-item">' +
        '<div style="flex:1;min-width:0;">' +
          '<div style="font-weight:600;font-size:13px;">' + esc(r.name) + '</div>' + filesHtml +
          '<div style="margin-top:4px;">' + statsHtml + '</div>' +
        '</div>' +
        '<div style="display:flex;gap:4px;align-items:center;flex-shrink:0;margin-left:12px;">' +
          '<span class="time" style="margin-right:4px;">' + r.time + '</span>' +
          '<a href="' + r.html_url + '" target="_blank" class="btn btn-primary" style="font-size:11px;padding:5px 10px;">查看</a>' +
          '<a href="' + r.html_url + '?download=1" class="btn btn-secondary" style="font-size:11px;padding:5px 10px;">HTML</a>' +
          (r.csv_url ? '<a href="' + r.csv_url + '" class="btn btn-secondary" style="font-size:11px;padding:5px 10px;">CSV</a>' : '') +
          (r.key_url ? '<a href="' + r.key_url + '" target="_blank" class="btn btn-secondary" style="font-size:11px;padding:5px 10px;background:#dc2626;color:#fff;">跟进</a>' : '') +
        '</div>' +
      '</div>';
    }).join('');
  });
}

function esc(s) { return (s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;'); }
function fmt(bytes) { return bytes < 1024 ? bytes+'B' : bytes < 1048576 ? (bytes/1024).toFixed(1)+'KB' : (bytes/1048576).toFixed(1)+'MB'; }

// ── Weekly Diagnosis Panel ────────────────────
var dealFile = null;

initTimeFilter('weekly-time-filter', function() { loadWeeklySummary(); });

function loadWeeklySummary() {
  var days = document.querySelector('#weekly-time-filter .time-chip.active').dataset.days || '7';
  Promise.all([
    fetch('/api/dashboard/weekly-summary?days=' + days).then(function(r) { return r.json(); }),
    fetch('/api/dashboard/scores?days=' + days).then(function(r) { return r.json(); })
  ]).then(function(results) {
      var data = results[0];
      var scores = results[1];
      var agents = Object.keys(data);
      if (!agents.length) {
        document.getElementById('weekly-summary-grid').innerHTML = '<div class="empty-state"><div class="icon">&#x1f4ca;</div>暂无询盘数据<br><small>请先上传聊天记录进行分析</small></div>';
        document.getElementById('analysis-agent-grid').innerHTML = '<div class="empty-state"><div class="icon">&#x1f4ca;</div>暂无数据</div>';
        return;
      }

      // ── Weekly Summary Text (aggregated) ──
      var totalInq = 0, totalSA = 0, totalB = 0, totalOrd = 0, totalBL = 0;
      var agentIssues = [];
      agents.forEach(function(agent) {
        var a = data[agent];
        totalInq += a.total || 0;
        totalSA += (a.s_count || 0) + (a.a_count || 0);
        totalB += a.b_count || 0;
        totalOrd += a.ordered || 0;
        totalBL += a.b_large || 0;
        if (a.top_issues && a.top_issues.length) {
          agentIssues.push(agent + ':' + a.top_issues[0].issue);
        }
      });
      var bPct = totalInq ? Math.round(totalB / totalInq * 100) : 0;
      var homePct = totalInq ? Math.round((totalInq - totalB) / totalInq * 100) : 0;
      var convPct = totalInq ? Math.round(totalOrd / totalInq * 100) : 0;
      var saPct = totalInq ? Math.round(totalSA / totalInq * 100) : 0;
      var avgScore = scores.length ? Math.round(scores.reduce(function(s, a) { return s + a.overall; }, 0) / scores.length) : '-';
      var issueDetail = agents.map(function(agent) {
        var a = data[agent];
        var issues = a.top_issues || [];
        var issueStr = issues.slice(0, 2).map(function(i) { return i.issue; }).join('、');
        return agent + '：' + (issueStr || '暂无突出问题');
      }).join('；');
      var bestAgent = agents.reduce(function(best, agent) {
        var r = data[agent];
        return (r.sa_rate || 0) > (data[best].sa_rate || 0) ? agent : best;
      });
      var summaryText = '本周共询盘<strong>' + totalInq + '</strong>个客户，S+A意向率<strong>' + saPct + '%</strong>，' +
        'B端占比<strong>' + bPct + '%</strong>（含<strong>' + totalBL + '</strong>个B端大客户），' +
        '家用询盘占<strong>' + homePct + '%</strong>，询盘转化率<strong>' + convPct + '%</strong>，' +
        '客服平均分<strong>' + avgScore + '</strong>。' +
        '各客服诊断：' + issueDetail + '。' +
        '综合建议：S+A率最高的客服是<strong>' + bestAgent + '</strong>，建议团队对标其沟通方式；' +
        '转化率偏低需重点关注黄金跟单期内（报价后72小时）的推进节奏，' +
        '对B端大客户须在一小时内完成深度需求挖掘并给出差异化方案。';
      document.getElementById('weekly-summary-text').innerHTML = summaryText;
      document.getElementById('weekly-summary-text').style.display = '';

      // ── Card 1: Analysis Results Summary (top) ──
      document.getElementById('stat-total-inquiries').textContent = totalInq;
      document.getElementById('stat-total-sa').textContent = totalSA;
      document.getElementById('stat-b-large').textContent = totalBL;
      document.getElementById('stat-ordered').textContent = totalOrd;
      document.getElementById('analysis-summary-time').textContent = '近' + days + '天';

      // Per-agent mini cards in analysis summary
      document.getElementById('analysis-agent-grid').innerHTML = agents.map(function(agent) {
        var a = data[agent];
        return '<div class="agent-score-card">' +
          '<div class="agent-header"><div class="agent-name" style="font-size:15px;">' + esc(agent) + '</div></div>' +
          '<div style="display:flex;gap:12px;margin:10px 0;font-size:12px;color:#6b7280;flex-wrap:wrap;">' +
            '<span>总询盘: <strong>' + a.total + '</strong></span>' +
            '<span style="color:#dc2626;">S: <strong>' + a.s_count + '</strong></span>' +
            '<span style="color:#d97706;">A: <strong>' + a.a_count + '</strong></span>' +
            '<span>S+A率: <strong>' + a.sa_rate + '%</strong></span>' +
            '<span style="color:#059669;">成交: <strong>' + a.ordered + '</strong></span>' +
          '</div>' +
          '<div style="margin-top:6px;font-size:10px;color:#9ca3af;">B端大客户: ' + a.b_large + ' &middot; B: ' + a.b_count + ' &middot; C: ' + a.c_count + ' &middot; 成交率: ' + a.order_rate + '%</div>' +
        '</div>';
      }).join('');

      // ── Card 2: Weekly Summary (scroll down) ──
      var grid = document.getElementById('weekly-summary-grid');
      grid.innerHTML = agents.map(function(agent) {
        var a = data[agent];
        var topIssues = (a.top_issues || []).map(function(i) {
          return '<span style="display:inline-block;background:#fef2f2;color:#dc2626;padding:2px 8px;border-radius:4px;font-size:10px;margin:2px;">' + esc(i.issue) + '(' + i.count + ')</span>';
        }).join('');
        var keyCusts = (a.key_customers || []).slice(0, 5).map(function(c) {
          return '<span style="display:inline-block;background:#f3f4f6;padding:2px 8px;border-radius:4px;font-size:10px;margin:2px;">' +
            '<span class="badge badge-' + c.level + '" style="font-size:9px;padding:1px 6px;">' + c.level + '</span> ' +
            esc(c.name) + ' ' + esc(c.product) +
          '</span>';
        }).join('');
        return '<div class="agent-score-card">' +
          '<div class="agent-header"><div class="agent-name" style="font-size:15px;">' + esc(agent) + '</div></div>' +
          '<div style="display:flex;gap:12px;margin:10px 0;font-size:12px;color:#6b7280;flex-wrap:wrap;">' +
            '<span>总询盘: <strong>' + a.total + '</strong></span>' +
            '<span style="color:#dc2626;">S: <strong>' + a.s_count + '</strong></span>' +
            '<span style="color:#d97706;">A: <strong>' + a.a_count + '</strong></span>' +
            '<span>S+A率: <strong>' + a.sa_rate + '%</strong></span>' +
            '<span style="color:#059669;">成交: <strong>' + a.ordered + '</strong></span>' +
          '</div>' +
          (topIssues ? '<div style="margin:8px 0;">' + topIssues + '</div>' : '') +
          (keyCusts ? '<div style="margin:8px 0;">' + keyCusts + '</div>' : '') +
          '<div style="margin-top:8px;font-size:10px;color:#9ca3af;">B端大客户: ' + a.b_large + ' &middot; B: ' + a.b_count + ' &middot; C: ' + a.c_count + ' &middot; 成交率: ' + a.order_rate + '%</div>' +
        '</div>';
      }).join('');
      if (document.getElementById('diagnosis-card').style.display !== 'none') loadDiagnosis();
    });
}

var dealDz = document.getElementById('deal-drop-zone');
var dealFi = document.getElementById('deal-file-input');
var btnDeal = document.getElementById('btn-upload-deal');

dealDz.addEventListener('click', function() { dealFi.click(); });
dealDz.addEventListener('dragover', function(e) { e.preventDefault(); dealDz.classList.add('dragover'); });
dealDz.addEventListener('dragleave', function() { dealDz.classList.remove('dragover'); });
dealDz.addEventListener('drop', function(e) {
  e.preventDefault(); dealDz.classList.remove('dragover');
  var dropFiles = Array.from(e.dataTransfer.files);
  if (dropFiles.length) { dealFile = dropFiles[0]; showDealFileInfo(); }
});
dealFi.addEventListener('change', function() {
  if (dealFi.files.length) { dealFile = dealFi.files[0]; showDealFileInfo(); }
});

function showDealFileInfo() {
  document.getElementById('deal-file-info').style.display = '';
  document.getElementById('deal-file-info').textContent = '已选择: ' + dealFile.name + ' (' + fmt(dealFile.size) + ')';
  btnDeal.disabled = false;
  document.getElementById('deal-status').textContent = '';
}

btnDeal.addEventListener('click', function() {
  if (!dealFile) return;
  var formData = new FormData();
  formData.append('file', dealFile);
  btnDeal.disabled = true; btnDeal.textContent = '解析中...';
  document.getElementById('deal-status').textContent = '';
  fetch('/api/upload-deals', { method: 'POST', body: formData })
    .then(function(r) { return r.json(); })
    .then(function(data) {
      btnDeal.textContent = '上传并解析'; btnDeal.disabled = false;
      if (data.status === 'ok') {
        var info = data.message;
        if (data.csv_headers) info += ' | 列: ' + data.csv_headers.join(', ');
        if (data.agent_distribution) {
          info += ' | ' + Object.keys(data.agent_distribution).map(function(a) {
            return a + '(' + data.agent_distribution[a] + '笔)';
          }).join(' ');
        }
        document.getElementById('deal-status').textContent = info;
        document.getElementById('deal-status').style.color = '#059669';
        loadDiagnosis();
      } else {
        document.getElementById('deal-status').textContent = '错误: ' + (data.error || '');
        document.getElementById('deal-status').style.color = '#dc2626';
      }
    });
});

function loadDiagnosis() {
  var days = document.querySelector('#weekly-time-filter .time-chip.active').dataset.days || '7';
  fetch('/api/dashboard/combined-diagnosis?days=' + days)
    .then(function(r) { return r.json(); })
    .then(function(data) {
      var card = document.getElementById('diagnosis-card');
      card.style.display = '';
      var inv = data.inquiry_summary || {};
      document.getElementById('diagnosis-overview').innerHTML =
        '<div class="result-stats">' +
          '<div class="result-stat"><div class="num">' + (inv.total_inquiries||0) + '</div><div class="lbl">总询盘数</div></div>' +
          '<div class="result-stat s"><div class="num">' + (inv.total_sa||0) + '</div><div class="lbl">S+A 高意向</div></div>' +
          '<div class="result-stat"><div class="num">' + (inv.total_ordered_inquiry||0) + '</div><div class="lbl">询盘成交</div></div>' +
          '<div class="result-stat"><div class="num">' + (inv.total_deals||0) + '</div><div class="lbl">成交表笔数</div></div>' +
          '<div class="result-stat a"><div class="num">' + fmtMoney(inv.total_deal_amount||0) + '</div><div class="lbl">成交总金额</div></div>' +
        '</div>';

      var agentView = data.agent_view || {};
      var agents = Object.keys(agentView);
      document.getElementById('agent-compare-tbody').innerHTML = agents.length ? agents.map(function(agent) {
        var v = agentView[agent];
        return '<tr>' +
          '<td style="font-weight:600;">' + esc(agent) + '</td>' +
          '<td>' + v.inquiry_total + '</td><td>' + v.inquiry_sa + '</td>' +
          '<td>' + (v.inquiry_sa_rate||0) + '%</td><td>' + v.inquiry_ordered + '</td>' +
          '<td>' + v.deal_count + '</td>' +
          '<td style="font-weight:600;color:#059669;">' + fmtMoney(v.deal_amount) + '</td>' +
          '<td style="font-size:11px;">' + (v.top_issues||[]).slice(0,2).map(function(i) { return esc(i.issue); }).join('<br>') + '</td>' +
        '</tr>';
      }).join('') : '<tr><td colspan="8" style="text-align:center;color:#9ca3af;">暂无数据</td></tr>';

      var missed = data.missed_opportunities || [];
      var missedTbody = document.getElementById('missed-tbody');
      var missedEmpty = document.getElementById('missed-empty');
      if (!missed.length) { missedTbody.innerHTML = ''; missedEmpty.style.display = ''; }
      else {
        missedEmpty.style.display = 'none';
        missedTbody.innerHTML = missed.map(function(m) {
          var ctype = m.type || '';
          return '<tr>' +
            '<td class="cust-name">' + esc(m.name) + '</td>' +
            '<td>' + esc(m.agent) + '</td>' +
            '<td><span class="badge badge-' + m.level + '">' + m.level + '</span></td>' +
            '<td><span class="badge ' + (ctype==='B端大客户'?'badge-blarge':'badge-b') + '">' + esc(ctype) + '</span></td>' +
            '<td>' + esc(m.product||'') + '</td>' +
            '<td style="font-size:12px;">' + esc(m.stage||'') + '</td>' +
          '</tr>';
        }).join('');
      }
      document.getElementById('deal-status').textContent = '已加载综合诊断';
      document.getElementById('deal-status').style.color = '#059669';
    });
}

function fmtMoney(v) {
  if (!v || v === 0) return '0';
  if (v >= 10000) return (v/10000).toFixed(1) + '万';
  return v.toLocaleString('zh-CN');
}

window.restartServer = function(btn) {
  if (!confirm('确定要重启服务吗？重启后页面会自动刷新（约3秒）。')) return;
  btn.disabled = true;
  btn.textContent = '⏳ 重启中...';
  fetch('/api/restart').catch(function() {});
  var attempts = 0;
  var timer = setInterval(function() {
    attempts++;
    fetch('/', {cache: 'no-store'}).then(function(r) {
      if (r.ok) {
        clearInterval(timer);
        location.reload();
      }
    }).catch(function() {});
    if (attempts > 30) {
      clearInterval(timer);
      btn.disabled = false;
      btn.textContent = '🔄 重启服务';
      alert('重启失败，请手动检查服务器');
    }
  }, 1000);
};

loadScores();
loadHistory();
})();
</script>
</body>
</html>
"""



def main():
    print("=" * 60)
    print("  销售客服分析诊断系统 - Web 服务")
    print("=" * 60)
    print(f"\n  本地访问: http://localhost:{PORT}")
    print(f"  局域网访问: http://<你的IP>:{PORT}")
    print(f"\n  按 Ctrl+C 停止服务\n")

    server = HTTPServer((HOST, PORT), Handler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n  服务已停止")
        server.shutdown()


if __name__ == "__main__":
    main()
