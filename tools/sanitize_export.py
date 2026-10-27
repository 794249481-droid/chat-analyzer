#!/usr/bin/env python3
"""Create a safer demo copy of an exported chat JSON file.

This is a convenience tool for local review. It does not claim to remove every
possible identifier, so the output must still be checked by a human before it
is shared with another service or committed to a repository.
"""

import argparse
import json
import re
from pathlib import Path


PHONE = re.compile(r"(?<!\d)(?:1[3-9]\d{9}|(?:\+?86[- ]?)?1[3-9]\d{9})(?!\d)")
EMAIL = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
WECHAT = re.compile(r"(?i)(微信(?:号)?|wechat)\s*[:：]?\s*[A-Za-z][-_A-Za-z0-9]{5,}")
ORDER = re.compile(r"(?i)(订单号|order\s*(?:no\.?|number))\s*[:：#]?\s*[A-Za-z0-9-]{5,}")


def clean_text(value: str) -> str:
    value = PHONE.sub("[PHONE]", value)
    value = EMAIL.sub("[EMAIL]", value)
    value = WECHAT.sub(lambda m: f"{m.group(1)} [ACCOUNT]", value)
    value = ORDER.sub(lambda m: f"{m.group(1)} [ORDER]", value)
    return value


def sanitize(value, counters=None, key=""):
    counters = counters if counters is not None else {"customer": 0, "agent": 0}
    lowered = key.lower()

    if isinstance(value, dict):
        return {k: sanitize(v, counters, k) for k, v in value.items()}
    if isinstance(value, list):
        return [sanitize(v, counters, key) for v in value]
    if not isinstance(value, str):
        return value

    if lowered in {"customer", "customer_name", "customername"}:
        counters["customer"] += 1
        return f"customer-{counters['customer']:03d}"
    if lowered in {"agent", "agent_name", "agentname", "speaker"}:
        counters["agent"] += 1
        return f"sales-{counters['agent']:03d}"
    return clean_text(value)


def main():
    parser = argparse.ArgumentParser(description="Create a sanitized copy of an exported chat JSON")
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()

    with args.input.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    cleaned = sanitize(data)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8") as handle:
        json.dump(cleaned, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    print(f"已生成脱敏副本: {args.output}")
    print("提醒：请人工检查输出，确认没有遗漏的姓名、账号、地址或商业机密。")


if __name__ == "__main__":
    main()
