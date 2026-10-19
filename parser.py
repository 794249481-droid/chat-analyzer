Warning: fs was declared with const; use let for reassignable variables.
"""Parse 1688 chat exports - JSON and image-based formats."""

import json
import os
import re
import base64
from pathlib import Path
from typing import Optional


# ── Folder name parsing ────────────────────────────────────────────

def parse_folder_name(folder_name: str) -> dict:
    """Parse folder name like '客服甲--客户乙-聊了15页'

    Returns dict with agent_name, customer_name, notes.

    Common patterns:
      客服甲--客户乙-聊了15页  → agent:客服甲, cust:客户乙
      客服乙--客户丙--聊了很多未下单 → agent:客服乙, cust:客户丙
      客服丙聊了很多只下单r201 → agent:客服丙, cust:r201
      客服丁--客户戊（仍在接待客户） → agent:客服丁, cust:客户戊
    """
    name = folder_name.strip()
    notes = ""
    agent_name = name
    customer_name = ""

    # Split on first '--' to separate agent from rest
    if "--" in name:
        parts = name.split("--", 1)
        agent_name = parts[0].strip()
        rest = parts[1].strip()

        # Try to extract customer name from rest
        # Customer name is the part before notes keywords, '(', '-r', or end
        notes_keywords = ["聊了很多", "聊了", "提成", "未下单", "下单", "接待客户", "还在"]

        # Find where notes start
        notes_start = len(rest)
        for kw in notes_keywords:
            idx = rest.find(kw)
            if 0 <= idx < notes_start:
                notes_start = idx

        # Also split on '（' (Chinese paren) as boundary
        paren_idx = rest.find("（")
        if 0 <= paren_idx < notes_start:
            notes_start = paren_idx

        # Customer name is before notes, strip trailing dashes/spaces
        customer_name = rest[:notes_start].strip().rstrip("-").strip()
        notes = rest[notes_start:].strip()
        return {"agent_name": agent_name, "customer_name": customer_name, "notes": notes}

    # No '--' separator: try to find agent/customer by keyword boundaries
    notes_keywords = ["聊了很多", "聊了", "提成", "未下单", "下单", "接待客户", "还在"]
    for kw in notes_keywords:
        idx = name.find(kw)
        if idx > 0:
            agent_name = name[:idx].strip().rstrip("-")
            note_part = name[idx:].strip()

            # Try to extract customer from end of notes (e.g., "...只下单r201")
            cust_match = re.search(r"[a-zA-Z]\w*\d+$", note_part)
            if cust_match:
                customer_name = cust_match.group()
                notes = note_part
            else:
                notes = note_part
            return {"agent_name": agent_name, "customer_name": customer_name, "notes": notes}

    return {"agent_name": name, "customer_name": "", "notes": ""}


# ── Image helpers ──────────────────────────────────────────────────

def image_to_base64(filepath: str) -> str:
    """Convert image file to base64 data URI."""
    ext = os.path.splitext(filepath)[1].lower()
    mime_map = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp"}
    mime = mime_map.get(ext, "image/png")
    with open(filepath, "rb") as f:
        b64 = base64.b64encode(f.read()).decode()
    return f"data:{mime};base64,{b64}"


def find_images(folder: str) -> list:
    """Find all chat screenshot images in a folder, sorted by name."""
    exts = {".png", ".jpg", ".jpeg", ".webp"}
    images = []
    for f in sorted(os.listdir(folder)):
        if Path(f).suffix.lower() in exts:
            images.append(os.path.join(folder, f))
    return images


# ── JSON parsing ───────────────────────────────────────────────────

def parse_content(content: str) -> dict:
    """Parse a single message content to extract speaker and clean text."""
    # Agent message example: "2026-07-01 14:04:30客服甲:Welcome..."
    m = re.match(r"^(\d{4}-\d{2}-\d{2}\s\d{2}:\d{2}:\d{2})chinagrd:(.+)", content)
    if m:
        timestamp = m.group(1)
        rest = m.group(2)
        agent_match = re.match(r"^(\S+?)(?=[一-鿿])", rest)
        if agent_match:
            agent_name = agent_match.group(1)
            text = rest[len(agent_name):]
        else:
            agent_name = ""
            text = rest
        return {
            "role": "agent",
            "speaker": agent_name,
            "text": text.strip(),
            "is_auto_reply": "客服甲" in agent_name,
            "timestamp": timestamp,
        }

    # Customer message: "{name}2026-07-01 14:04:30{text}"
    m = re.match(r"^(\S+?)(\d{4}-\d{2}-\d{2}\s\d{2}:\d{2}:\d{2})(.+)", content)
    if m:
        return {
            "role": "customer",
            "speaker": m.group(1),
            "text": m.group(3).strip(),
            "is_auto_reply": False,
            "timestamp": m.group(2),
        }

    ts_match = re.search(r"(\d{4}-\d{2}-\d{2}\s\d{2}:\d{2}:\d{2})", content)
    return {
        "role": "unknown",
        "speaker": "",
        "text": content.strip(),
        "is_auto_reply": False,
        "timestamp": ts_match.group(1) if ts_match else "",
    }


def _clean_conversations(conversations: dict) -> dict:
    cleaned = {}
    for cust_name, msgs in conversations.items():
        filtered = []
        for m in msgs:
            text = m.get("text", "")
            if not text:
                continue
            if "欢迎光临" in text and "官方认证源头商家" in text:
                continue
            if "马上为你转人工服务" in text:
                continue
            if "转交给" in text and m.get("role") == "customer":
                continue
            filtered.append(m)
        cleaned[cust_name] = filtered
    return cleaned


def parse_json_export(filepath: str) -> dict:
    """Parse a 1688 JSON chat export file.

    Supports two formats:
      1. Flat:  {"metadata": ..., "messages": [{"customer": ..., "content": ...}, ...]}
      2. Grouped: {"metadata": ..., "customers": [{"customer_name": ..., "messages": [...]}, ...]}
    """
    with open(filepath, "r", encoding="utf-8") as f:
        data = json.load(f)

    metadata = data.get("metadata", {})
    agent_name = metadata.get("agent", {}).get("name", "Unknown")
    conversations = {}

    if "customers" in data:
        for cust in data["customers"]:
            customer_name = cust.get("customer_name", "unknown")
            conversations[customer_name] = []
            for msg in cust.get("messages", []):
                parsed = parse_content(msg.get("content", ""))
                parsed["msg_id"] = msg.get("msg_id", "")
                parsed["raw_timestamp"] = msg.get("timestamp", parsed.get("timestamp", ""))
                conversations[customer_name].append(parsed)
    else:
        raw_messages = data.get("messages", [])
        for msg in raw_messages:
            customer = msg.get("customer", "unknown")
            parsed = parse_content(msg.get("content", ""))
            parsed["msg_id"] = msg.get("msg_id", "")
            parsed["raw_timestamp"] = msg.get("timestamp", parsed.get("timestamp", ""))

            if customer not in conversations:
                conversations[customer] = []
            conversations[customer].append(parsed)

    return {
        "metadata": metadata,
        "agent_name": agent_name,
        "conversations": _clean_conversations(conversations),
        "source": "json",
    }


# ── Image-based folder parsing ─────────────────────────────────────

def parse_chat_folders(root_dir: str) -> dict:
    """Scan chat records root directory for customer folders with images.

    Returns dict compatible with parse_json_export output.
    """
    root = Path(root_dir)
    conversations = {}

    # Try to determine agent name from first folder
    global_agent = None
    folders = sorted([d for d in root.iterdir() if d.is_dir()])

    for folder in folders:
        images = find_images(str(folder))
        if not images:
            continue

        info = parse_folder_name(folder.name)
        agent = info.get("agent_name", "")
        customer = info.get("customer_name", folder.name)

        if global_agent is None:
            global_agent = agent

        conversations[customer] = {
            "agent_name": agent,
            "customer_name": customer,
            "notes": info.get("notes", ""),
            "images": images,
            "folder": str(folder),
        }

    return {
        "metadata": {"source": "images", "root_dir": str(root)},
        "agent_name": global_agent or "Unknown",
        "conversations": conversations,
        "source": "images",
    }


def conversation_to_text(msgs: list, agent_name: str) -> str:
    """Convert parsed messages to a clean text transcript."""
    lines = []
    for m in msgs:
        role = m.get("role", "unknown")
        ts = m.get("timestamp", m.get("raw_timestamp", ""))
        text = m.get("text", "")
        if role == "agent":
            speaker = m.get("speaker", "") or agent_name
            lines.append(f"[{ts}] {speaker}(客服): {text}")
        elif role == "customer":
            lines.append(f"[{ts}] 顾客: {text}")
        else:
            lines.append(f"[{ts}] {text}")
    return "\n".join(lines)
