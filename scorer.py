Warning: fs was declared with const; use let for reassignable variables.
"""Agent scoring engine based on sales brain methodology."""

import json
import os
from pathlib import Path
from datetime import datetime, timedelta
from collections import defaultdict

OUTPUT_DIR = Path(__file__).parent / "output"

SCORE_WEIGHTS = {
    "communication": 0.35,
    "proactiveness": 0.35,
    "conversion": 0.30,
}


def _parse_agent_and_time(filename: str) -> tuple:
    stem = filename.replace(".json", "")
    idx = stem.rfind("_202")
    if idx > 0:
        agent = stem[:idx]
        ts_str = stem[idx + 1:]
        try:
            ts = datetime.strptime(ts_str, "%Y%m%d_%H%M%S")
            return agent, ts
        except ValueError:
            pass
    return None, None


def load_all_results(days: int = 30) -> dict:
    cutoff = datetime.now() - timedelta(days=days)
    agent_data = defaultdict(list)

    for f in sorted(OUTPUT_DIR.glob("*.json")):
        agent, ts = _parse_agent_and_time(f.name)
        if not agent or not ts or ts < cutoff:
            continue
        try:
            with open(f, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            if isinstance(data, list) and len(data) > 5:
                agent_data[agent].append((ts, data))
        except (json.JSONDecodeError, IOError):
            pass

    return agent_data


def _dedup_by_customer(results: list) -> list:
    """Deduplicate results by customer_name, keeping last occurrence (latest batch)."""
    seen = {}
    for r in results:
        name = r.get("customer_name", "")
        if name:
            seen[name] = r
    return list(seen.values())


def score_agent(results: list) -> dict:
    """Score a single batch of analysis results with 3 dimensions.

    Communication (沟通力): measures issue frequency and severity.
    Proactiveness (积极度): measures follow-up quality and initiative.
    Conversion (转化率): measures pipeline quality and deal closure.
    """
    results = _dedup_by_customer(results)
    total = len(results)
    if total == 0:
        return {
            "overall": 0, "communication": 0, "proactiveness": 0,
            "conversion": 0, "total_customers": 0,
            "s_count": 0, "a_count": 0, "ordered_count": 0,
            "sa_rate": 0,
        }

    # ── Communication Score (沟通力) ──────────────────────────────
    # Severity weights tuned for differentiation: 高=8, 中=3, 低=0.5
    # Formula: max(10, 100 - avg_penalty_per_customer)
    # Clean=100, 1 high/cust=92, 2 high+1 med/cust=81
    total_penalty = 0
    for r in results:
        for iss in r.get("communication_issues", []):
            sev = iss.get("severity", "低")
            if sev == "高":
                total_penalty += 8
            elif sev == "中":
                total_penalty += 3
            else:
                total_penalty += 0.5

    avg_penalty = total_penalty / total
    communication_score = max(10, 100 - avg_penalty)

    # ── Proactiveness Score (积极度) ──────────────────────────────
    # Per-customer scoring based on follow_up_actions quality
    per_customer_scores = []
    for r in results:
        actions = r.get("follow_up_actions", [])
        if not actions:
            per_customer_scores.append(0)
            continue

        num_actions = min(len(actions), 3)
        has_commitment = any(
            a.get("commitment_goal", "").strip() for a in actions
            if isinstance(a, dict)
        )
        has_fallback = any(
            a.get("fallback", "").strip() for a in actions
            if isinstance(a, dict)
        )
        has_priority = any(
            a.get("priority", "").strip() for a in actions
            if isinstance(a, dict)
        )
        has_red_flags = len(r.get("red_flags", [])) > 0

        score = min(100,
            num_actions * 20 +
            (15 if has_commitment else 0) +
            (10 if has_fallback else 0) +
            (10 if has_priority else 0) +
            (5 if has_red_flags else 0)
        )
        per_customer_scores.append(score)

    proactiveness_score = sum(per_customer_scores) / total

    # ── Conversion Score (转化率) ─────────────────────────────────
    s_count = sum(1 for r in results if r.get("customer_level") == "S")
    a_count = sum(1 for r in results if r.get("customer_level") == "A")

    closed_stages = {"已经成交", "售后中", "交付验收"}
    closed_count = sum(1 for r in results
                       if r.get("sales_stage", "") in closed_stages
                       or r.get("order_status") == "已下单")

    ordered = sum(1 for r in results if r.get("order_status") == "已下单")
    close_count = max(closed_count, ordered)

    sa_rate = (s_count + a_count) / total
    close_rate = close_count / total

    conversion_score = min(100, sa_rate * 60 + close_rate * 40)

    # ── Overall ───────────────────────────────────────────────────
    overall = (
        communication_score * SCORE_WEIGHTS["communication"]
        + proactiveness_score * SCORE_WEIGHTS["proactiveness"]
        + conversion_score * SCORE_WEIGHTS["conversion"]
    )

    return {
        "overall": round(overall, 1),
        "communication": round(communication_score, 1),
        "proactiveness": round(proactiveness_score, 1),
        "conversion": round(conversion_score, 1),
        "total_customers": total,
        "s_count": s_count,
        "a_count": a_count,
        "ordered_count": ordered,
        "sa_rate": round(sa_rate * 100, 1),
        "close_rate": round(close_rate * 100, 1),
    }


def get_agent_scores(days: int = 30) -> list:
    agent_data = load_all_results(days)
    result = []

    for agent, batches in agent_data.items():
        all_results = []
        latest_ts = None
        for ts, data in batches:
            all_results.extend(data)
            if latest_ts is None or ts > latest_ts:
                latest_ts = ts

        scores = score_agent(all_results)
        scores["agent_name"] = agent
        scores["batch_count"] = len(batches)
        scores["total_analyzed"] = len(all_results)
        scores["latest_time"] = latest_ts.strftime("%m-%d %H:%M") if latest_ts else ""
        result.append(scores)

    result.sort(key=lambda x: x["overall"], reverse=True)
    return result


def get_key_customers(days: int = 30) -> list:
    agent_data = load_all_results(days)
    key = []

    for agent, batches in agent_data.items():
        seen = set()
        for ts, data in batches:
            for r in data:
                name = r.get("customer_name", "")
                if (r.get("customer_level") in ("S", "A")
                        and r.get("order_status") != "已下单"
                        and name not in seen):
                    seen.add(name)
                    overview = _build_overview(r)
                    key.append({
                        "agent_name": agent,
                        "customer_name": name,
                        "customer_level": r.get("customer_level", ""),
                        "customer_type": r.get("customer_type", ""),
                        "is_b_large": r.get("is_b_large", False),
                        "sales_stage": r.get("sales_stage", ""),
                        "overview": overview,
                        "analysis_time": ts.strftime("%m-%d %H:%M"),
                    })

    level_order = {"S": 0, "A": 1}
    key.sort(key=lambda x: level_order.get(x["customer_level"], 99))
    return key


def get_pending_followups(days: int = 1) -> list:
    """Identify yesterday's high-potential customers that need immediate follow-up.

    Criteria:
    - S or A level (clear intent)
    - Not yet ordered
    - Substantial conversation (both sides engaged, high msg count)
    - Still in negotiation stage (not closed, not dead)
    - Assessment suggests the deal is still winnable
    """
    agent_data = load_all_results(days)
    pending = []
    positive_signals = {"值得跟", "快逼单", "大胆冲", "加速推进", "逼单", "跟进"}

    for agent, batches in agent_data.items():
        seen = set()
        for ts, data in batches:
            for r in data:
                name = r.get("customer_name", "")
                if name in seen:
                    continue
                level = r.get("customer_level", "")
                if level not in ("S", "A"):
                    continue
                if r.get("order_status") == "已下单":
                    continue

                stage = r.get("sales_stage", "")
                msg_count = r.get("_msg_count", 0)
                assessment = r.get("overall_assessment", "")
                level_reason = r.get("level_reason", "")

                # Must have substantial engagement: >= 8 messages
                if msg_count < 8:
                    continue

                # Must be in active negotiation (not dead/abandoned)
                if stage not in ("正在谈判", "已经加微信", "谈判成交"):
                    if stage in ("已经成交", "售后中"):
                        continue

                # Assessment must contain positive signal
                combined = assessment + level_reason
                has_signal = any(s in combined for s in positive_signals)
                if not has_signal:
                    continue

                seen.add(name)
                overview = _build_overview(r)
                pending.append({
                    "agent_name": agent,
                    "customer_name": name,
                    "customer_level": level,
                    "customer_type": r.get("customer_type", ""),
                    "is_b_large": r.get("is_b_large", False),
                    "sales_stage": stage,
                    "msg_count": msg_count,
                    "overview": overview,
                    "analysis_time": ts.strftime("%m-%d %H:%M"),
                })

    # Sort: S first, then by msg_count descending (more engaged = higher)
    level_order = {"S": 0, "A": 1}
    pending.sort(key=lambda x: (level_order.get(x["customer_level"], 99),
                                 -(x.get("msg_count", 0))))
    return pending


def _build_overview(r: dict) -> str:
    """Build a concise overview: key needs + stage reason analysis."""
    key_info = r.get("key_info", {}) or {}
    product = key_info.get("product_interest", "")
    quantity = key_info.get("quantity", "")
    stage = r.get("sales_stage", "")
    level_reason = r.get("level_reason", "")

    parts = []
    if product:
        parts.append(product[:8])
    if quantity:
        parts.append(quantity[:6])

    need_part = "需求:" + ("、".join(parts) if parts else "待确认")

    stage_map = {
        "正在谈判": "仍在沟通方案中",
        "已经加微信": "已转微信跟进",
        "已经成交": "已成交待交付",
        "售后中": "售后处理中",
    }
    stage_reason = stage_map.get(stage, stage)

    if level_reason:
        short_reason = level_reason[:15]
        stage_reason = short_reason

    overview = f"{need_part} | {stage_reason}"
    if len(overview) > 20:
        overview = overview[:20]
    return overview


# ── Weekly Summary & Deal Integration ──────────────────────────────

def get_weekly_summary(days: int = 7) -> dict:
    """Generate a weekly inquiry diagnostic summary across all agents."""
    agent_data = load_all_results(days)
    agents_summary = {}

    for agent, batches in agent_data.items():
        all_results = []
        for ts, data in batches:
            all_results.extend(data)

        all_results = _dedup_by_customer(all_results)

        total = len(all_results)
        if total == 0:
            continue

        s_count = sum(1 for r in all_results if r.get("customer_level") == "S")
        a_count = sum(1 for r in all_results if r.get("customer_level") == "A")
        b_count = sum(1 for r in all_results if r.get("customer_level") == "B")
        c_count = sum(1 for r in all_results if r.get("customer_level") == "C")
        b_large = sum(1 for r in all_results if r.get("is_b_large"))
        ordered = sum(1 for r in all_results if r.get("order_status") == "已下单")

        # Top issues summary
        issue_types = defaultdict(int)
        for r in all_results:
            for iss in r.get("communication_issues", []):
                issue_types[iss.get("issue", "")[:30]] += 1
        top_issues = sorted(issue_types.items(), key=lambda x: -x[1])[:3]

        # Sales stage distribution
        stage_dist = defaultdict(int)
        for r in all_results:
            stage_dist[r.get("sales_stage", "未知")] += 1

        # Key S/A customers (not ordered)
        key_customers = []
        seen = set()
        for r in all_results:
            name = r.get("customer_name", "")
            if (r.get("customer_level") in ("S", "A")
                    and r.get("order_status") != "已下单"
                    and name not in seen):
                seen.add(name)
                key_customers.append({
                    "name": name,
                    "level": r.get("customer_level"),
                    "type": "B端大客户" if r.get("is_b_large") else r.get("customer_type", "家用"),
                    "product": (r.get("key_info", {}) or {}).get("product_interest", "")[:15],
                    "stage": r.get("sales_stage", ""),
                })

        agents_summary[agent] = {
            "total": total,
            "s_count": s_count,
            "a_count": a_count,
            "b_count": b_count,
            "c_count": c_count,
            "b_large": b_large,
            "ordered": ordered,
            "sa_rate": round((s_count + a_count) / total * 100, 1),
            "order_rate": round(ordered / max(1, total) * 100, 1),
            "top_issues": [{"issue": i, "count": c} for i, c in top_issues],
            "stage_distribution": dict(stage_dist),
            "key_customers": key_customers[:5],
        }

    return agents_summary


def parse_deal_csv(filepath: str) -> tuple:
    """Parse uploaded deal/sales CSV/TSV file.

    Auto-detects delimiter (tab/comma) and column mapping.
    Known columns: 日期, 销售客服, 旺旺名称ID, 产品型号, 货品总价, etc.
    """
    import csv
    import io
    import re

    deals = []
    headers = []

    with open(filepath, "r", encoding="utf-8-sig") as f:
        raw = f.read()

    if not raw.strip():
        return deals, headers

    # Auto-detect delimiter
    try:
        dialect = csv.Sniffer().sniff(raw[:4096], delimiters="\t,;")
        delimiter = dialect.delimiter
    except Exception:
        # Fallback: count tabs vs commas in first line
        first_line = raw.strip().split("\n")[0]
        if first_line.count("\t") > first_line.count(","):
            delimiter = "\t"
        else:
            delimiter = ","

    # For comma-delimited files, fix amounts like ¥58,000.00 that split across columns
    if delimiter == ",":
        amount_re = re.compile(r'^[¥$€£]?\d[\d,]*\.?\d*$')
        fixed_lines = []
        for line in raw.strip().split("\n"):
            if not line.strip():
                continue
            reader = csv.reader(io.StringIO(line))
            fields = next(reader)
            if not headers:
                headers = [f.strip() for f in fields]
                fixed_lines.append(line)
                continue

            expected = len(headers)
            if len(fields) <= expected:
                fixed_lines.append(line)
                continue

            merged = []
            i = 0
            while i < len(fields):
                f = fields[i].strip()
                if i + 1 < len(fields) and amount_re.match(f):
                    next_f = fields[i + 1].strip()
                    if re.match(r'^\d{1,3}(\.\d+)?$', next_f):
                        combined = f + next_f
                        if i + 2 < len(fields) and re.match(r'^\d{1,3}(\.\d+)?$', fields[i + 2].strip()):
                            combined += fields[i + 2].strip()
                            i += 3
                        else:
                            i += 2
                        merged.append(combined)
                        continue
                merged.append(f)
                i += 1

            fixed_lines.append(",".join(
                '"' + f.replace('"', '""') + '"' if "," in f else f
                for f in merged
            ))
        raw = "\n".join(fixed_lines)

    # Parse with detected delimiter
    reader = csv.DictReader(io.StringIO(raw), delimiter=delimiter)
    headers = reader.fieldnames or []

    # Column mapping patterns — ordered from specific to general
    CUSTOMER_KEYS = ["旺旺", "客户名称", "客户id", "customer", "客户名", "客户", "买家名称", "买家"]
    AMOUNT_KEYS = ["货品总价", "成交金额", "金额", "amount", "总价", "价格", "成交价", "实付", "订单金额"]
    DATE_KEYS = ["成交日期", "日期", "date", "下单时间", "成交时间", "付款时间", "时间"]
    AGENT_KEYS = ["销售客服", "客服名称", "客服", "agent", "销售员", "销售", "负责人", "业务员", "跟单", "接单", "销售人员"]
    PRODUCT_KEYS = ["产品名称", "产品型号", "详细名称", "产品", "product", "商品名称", "商品", "货品"]

    for row in reader:
        if not any(v.strip() for v in row.values()):
            continue
        deal = {
            "customer": "",
            "amount": "",
            "date": "",
            "agent": "",
            "product": "",
        }
        for k, v in row.items():
            if k is None or not v:
                continue
            kl = k.strip().lower()
            if any(w in kl for w in CUSTOMER_KEYS):
                deal["customer"] = v.strip()
            elif any(w in kl for w in AMOUNT_KEYS):
                deal["amount"] = v.strip()
            elif any(w in kl for w in DATE_KEYS):
                deal["date"] = v.strip()
            elif any(w in kl for w in AGENT_KEYS):
                deal["agent"] = v.strip()
            elif any(w in kl for w in PRODUCT_KEYS):
                if not deal["product"]:
                    deal["product"] = v.strip()
        if deal["customer"] or deal["amount"]:
            deals.append(deal)
    return deals, headers


def combined_diagnosis(days: int = 7, deals: list = None) -> dict:
    """Cross-reference inquiry analysis with actual deal data."""
    deals = deals or []
    weekly = get_weekly_summary(days)

    # Aggregate inquiry data
    total_inquiries = sum(a["total"] for a in weekly.values())
    total_sa = sum(a["s_count"] + a["a_count"] for a in weekly.values())
    total_ordered_inquiry = sum(a["ordered"] for a in weekly.values())

    # Map deal CSV agent names to inquiry system agent names
    DEAL_AGENT_MAP = {
        "原始客服名A": "客服甲",
        "原始客服名B": "客服乙",
        "原始客服名C": "客服丙",
        "原始客服名D": "客服丁",
    }

    total_deals = len(deals)
    deal_agents = defaultdict(int)
    deal_amounts = defaultdict(float)
    for d in deals:
        raw_agent = d.get("agent", "未知")
        agent = DEAL_AGENT_MAP.get(raw_agent, raw_agent)
        deal_agents[agent] += 1
        try:
            amt_str = str(d.get("amount", "0")).replace(",", "").replace("¥", "").replace(" ", "").replace("元", "")
            deal_amounts[agent] += float(amt_str)
        except (ValueError, AttributeError) as e:
            print(f"[DIAGNOSIS] Amount parse error: agent={agent} raw={d.get('amount')} err={e}")

    # Per-agent combined view
    agent_view = {}
    all_agents = set(list(weekly.keys()) + list(deal_agents.keys()))
    for agent in all_agents:
        inquiry = weekly.get(agent, {})
        agent_view[agent] = {
            "inquiry_total": inquiry.get("total", 0),
            "inquiry_sa": inquiry.get("s_count", 0) + inquiry.get("a_count", 0),
            "inquiry_ordered": inquiry.get("ordered", 0),
            "inquiry_sa_rate": inquiry.get("sa_rate", 0),
            "deal_count": deal_agents.get(agent, 0),
            "deal_amount": round(deal_amounts.get(agent, 0), 2),
            "top_issues": inquiry.get("top_issues", []),
        }

    # Find gaps: high-potential inquiries not in deal list.
    # Normalize customer names for matching: inquiry uses "wangwangID(1688)"
    # while deal CSV uses just "wangwangID".
    def _normalize(name: str) -> str:
        """Strip platform suffix like (1688) or (淘宝) for comparison."""
        import re
        return re.sub(r'\([^)]*\)$', '', name.strip())

    deal_customers_raw = {d.get("customer", "") for d in deals}
    deal_customers = {_normalize(c) for c in deal_customers_raw}
    missed_opportunities = []
    for agent, info in weekly.items():
        for c in info.get("key_customers", []):
            if _normalize(c["name"]) not in deal_customers:
                missed_opportunities.append({
                    **c,
                    "agent": agent,
                })

    return {
        "period": f"近{days}天",
        "inquiry_summary": {
            "total_inquiries": total_inquiries,
            "total_sa": total_sa,
            "total_ordered_inquiry": total_ordered_inquiry,
            "total_deals": total_deals,
            "total_deal_amount": round(sum(deal_amounts.values()), 2),
        },
        "agent_view": agent_view,
        "missed_opportunities": missed_opportunities[:10],
        "weekly_detail": weekly,
    }
