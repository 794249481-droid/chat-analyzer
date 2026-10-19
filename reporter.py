Warning: fs was declared with const; use let for reassignable variables.
"""Generate HTML diagnostic reports."""

import json
import os
from datetime import datetime


def _level_badge(level: str) -> str:
    colors = {
        "S": "#dc2626",
        "A": "#ea580c",
        "B": "#ca8a04",
        "C": "#6b7280",
    }
    c = colors.get(level, "#6b7280")
    return f'<span style="display:inline-block;padding:2px 10px;border-radius:12px;background:{c};color:#fff;font-weight:700;font-size:13px;">{level}</span>'


def _type_badge(ctype: str) -> str:
    if "B端" in ctype:
        c = "#7c3aed"
    else:
        c = "#0891b2"
    return f'<span style="display:inline-block;padding:2px 8px;border-radius:4px;background:{c};color:#fff;font-size:12px;">{ctype}</span>'


def _severity_badge(severity: str) -> str:
    colors = {"高": "#dc2626", "中": "#ea580c", "低": "#6b7280"}
    c = colors.get(severity, "#6b7280")
    return f'<span style="color:{c};font-weight:600;">{severity}</span>'


def generate_html(results: list, summary: str, agent_name: str, metadata: dict) -> str:
    """Generate a complete HTML report."""
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    total = len(results)
    s_count = sum(1 for r in results if r.get("customer_level") == "S")
    a_count = sum(1 for r in results if r.get("customer_level") == "A")
    b_count = sum(1 for r in results if r.get("customer_level") == "B")
    c_count = sum(1 for r in results if r.get("customer_level") == "C")
    b_clients = sum(1 for r in results if "B端" in str(r.get("customer_type", "")))
    b_large = sum(1 for r in results if r.get("is_b_large"))

    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>销售客服诊断报告 - {agent_name}</title>
<style>
* {{ margin: 0; padding: 0; box-sizing: border-box; }}
body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif; background: #f5f5f5; color: #1f2937; line-height: 1.6; }}
.container {{ max-width: 1200px; margin: 0 auto; padding: 24px; }}
.header {{ background: linear-gradient(135deg, #1e3a5f, #2563eb); color: #fff; padding: 32px; border-radius: 12px; margin-bottom: 24px; }}
.header h1 {{ font-size: 24px; margin-bottom: 8px; }}
.header .meta {{ font-size: 14px; opacity: 0.85; }}
.stats-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(140px, 1fr)); gap: 12px; margin-bottom: 24px; }}
.stat-card {{ background: #fff; border-radius: 10px; padding: 20px; text-align: center; box-shadow: 0 1px 3px rgba(0,0,0,0.08); }}
.stat-card .value {{ font-size: 32px; font-weight: 700; }}
.stat-card .label {{ font-size: 13px; color: #6b7280; margin-top: 4px; }}
.stat-card.s {{ border-left: 4px solid #dc2626; }}
.stat-card.a {{ border-left: 4px solid #ea580c; }}
.stat-card.b {{ border-left: 4px solid #ca8a04; }}
.stat-card.c {{ border-left: 4px solid #6b7280; }}
.section {{ background: #fff; border-radius: 10px; padding: 24px; margin-bottom: 16px; box-shadow: 0 1px 3px rgba(0,0,0,0.08); }}
.section h2 {{ font-size: 18px; margin-bottom: 16px; padding-bottom: 8px; border-bottom: 2px solid #e5e7eb; }}
.customer-card {{ border: 1px solid #e5e7eb; border-radius: 8px; padding: 16px; margin-bottom: 12px; }}
.customer-card.s-level {{ border-left: 4px solid #dc2626; background: #fef2f2; }}
.customer-card.a-level {{ border-left: 4px solid #ea580c; background: #fff7ed; }}
.customer-card:hover {{ box-shadow: 0 2px 8px rgba(0,0,0,0.08); }}
.customer-header {{ display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px; flex-wrap: wrap; gap: 8px; }}
.customer-header .name {{ font-size: 16px; font-weight: 600; }}
.customer-header .badges {{ display: flex; gap: 6px; flex-wrap: wrap; }}
.issue-list {{ list-style: none; padding: 0; }}
.issue-list li {{ padding: 6px 0; border-bottom: 1px solid #f3f4f6; font-size: 14px; }}
.issue-list li:last-child {{ border-bottom: none; }}
.actions {{ padding: 0; list-style: none; }}
.actions li {{ padding: 4px 0 4px 16px; position: relative; font-size: 14px; }}
.actions li::before {{ content: "→"; position: absolute; left: 0; color: #2563eb; }}
.info-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 8px; font-size: 13px; margin: 8px 0; }}
.info-grid .info-item {{ padding: 6px 10px; background: #f9fafb; border-radius: 4px; }}
.info-item strong {{ color: #374151; }}
.assessment {{ font-size: 14px; color: #4b5563; font-style: italic; margin-top: 8px; padding: 8px; background: #f9fafb; border-radius: 4px; }}
.summary-content {{ font-size: 14px; line-height: 1.8; }}
.summary-content h2 {{ font-size: 16px; margin: 16px 0 8px; }}
.summary-content h3 {{ font-size: 14px; margin: 12px 0 6px; }}
.summary-content ul {{ padding-left: 20px; }}
.summary-content li {{ margin: 4px 0; }}
.filter-bar {{ margin-bottom: 16px; display: flex; gap: 8px; flex-wrap: wrap; }}
.filter-btn {{ padding: 6px 16px; border: 1px solid #d1d5db; border-radius: 20px; background: #fff; cursor: pointer; font-size: 13px; }}
.filter-btn.active {{ background: #2563eb; color: #fff; border-color: #2563eb; }}
</style>
</head>
<body>
<div class="container">

<div class="header">
  <h1>销售客服沟通诊断报告</h1>
  <div class="meta">
    客服: {agent_name} | 平台: 1688 | 生成时间: {now}<br>
    数据范围: {metadata.get('date_range', {}).get('start', '-')} ~ {metadata.get('date_range', {}).get('end', '-')} |
    分析客户数: {total}
  </div>
</div>

<div class="stats-grid">
  <div class="stat-card s"><div class="value">{s_count}</div><div class="label">S级 · 紧急跟进</div></div>
  <div class="stat-card a"><div class="value">{a_count}</div><div class="label">A级 · 重点跟进</div></div>
  <div class="stat-card b"><div class="value">{b_count}</div><div class="label">B级 · 常规跟进</div></div>
  <div class="stat-card c"><div class="value">{c_count}</div><div class="label">C级 · 低优先级</div></div>
  <div class="stat-card" style="border-left:4px solid #7c3aed;"><div class="value">{b_clients}</div><div class="label">B端客户</div></div>
  <div class="stat-card" style="border-left:4px solid #7c3aed;background:#faf5ff;"><div class="value">{b_large}</div><div class="label">B端大客户</div></div>
</div>

<div class="section">
  <h2>客服整体表现总结</h2>
  <div class="summary-content">
{_markdown_to_html(summary)}
  </div>
</div>

<div class="section">
  <h2>客户详细诊断</h2>
  <div class="filter-bar">
    <button class="filter-btn active" onclick="filterLevel('all')">全部</button>
    <button class="filter-btn" onclick="filterLevel('S')" style="color:#dc2626;font-weight:600;">S级</button>
    <button class="filter-btn" onclick="filterLevel('A')" style="color:#ea580c;">A级</button>
    <button class="filter-btn" onclick="filterLevel('B')" style="color:#ca8a04;">B级</button>
    <button class="filter-btn" onclick="filterLevel('C')" style="color:#6b7280;">C级</button>
    <button class="filter-btn" onclick="filterType('B端')" style="color:#7c3aed;">B端客户</button>
    <button class="filter-btn" onclick="filterType('B端大客户')" style="color:#7c3aed;font-weight:600;">B端大客户</button>
  </div>
  <div id="customer-list">
{_generate_customer_cards(results)}
  </div>
</div>

</div>
<script>
function filterLevel(level) {{
  document.querySelectorAll('.filter-btn').forEach(b => b.classList.remove('active'));
  event.target.classList.add('active');
  document.querySelectorAll('.customer-card').forEach(card => {{
    if (level === 'all') card.style.display = '';
    else card.style.display = card.dataset.level === level ? '' : 'none';
  }});
}}
function filterType(type) {{
  document.querySelectorAll('.filter-btn').forEach(b => b.classList.remove('active'));
  event.target.classList.add('active');
  document.querySelectorAll('.customer-card').forEach(card => {{
    if (type === 'B端大客户') card.style.display = card.dataset.blarge === 'true' ? '' : 'none';
    else card.style.display = card.dataset.type.includes('B端') ? '' : 'none';
  }});
}}
</script>
</body>
</html>"""
    return html


def _generate_customer_cards(results: list) -> str:
    cards = []
    for r in results:
        if r.get("error"):
            continue

        level = r.get("customer_level", "C")
        ctype = r.get("customer_type", "未知")
        is_bl = r.get("is_b_large", False)
        name = r.get("customer_name", "未知")
        msg_count = r.get("_msg_count", 0)

        level_class = f"{level.lower()}-level" if level in "SABC" else ""

        # Issues with fix scripts
        issues_html = ""
        issues = r.get("communication_issues", [])
        if issues:
            issues_html = '<ul class="issue-list">'
            for iss in issues[:5]:
                sev = _severity_badge(iss.get("severity", "低"))
                fix = iss.get("fix", "")
                fix_html = f' <span style="color:#2563eb;">→ {fix}</span>' if fix else ""
                issues_html += f'<li>[{sev}] {iss.get("issue", "")} — <span style="color:#9ca3af;">"{iss.get("example", "")}"</span>{fix_html}</li>'
            issues_html += "</ul>"

        # Key info
        key = r.get("key_info", {})
        info_html = ""
        if key:
            info_html = '<div class="info-grid">'
            labels = {"product_interest": "产品意向", "quantity": "需求量", "budget": "预算",
                      "timeline": "时间节点", "pain_points": "痛点", "decision_chain": "决策链",
                      "competitor_mention": "竞品信息"}
            for k, v in key.items():
                if v:
                    label = labels.get(k, k)
                    info_html += f'<div class="info-item"><strong>{label}:</strong> {v}</div>'
            info_html += "</div>"

        # Follow-up actions (supports both list of strings and list of objects)
        actions = r.get("follow_up_actions", [])
        actions_html = ""
        if actions:
            actions_html = '<ul class="actions">'
            for a in actions[:5]:
                if isinstance(a, dict):
                    priority = a.get("priority", "")
                    act = a.get("action", "")
                    goal = a.get("commitment_goal", "")
                    goal_text = f" <span style='color:#2563eb;font-size:12px;'>[{goal}]</span>" if goal else ""
                    actions_html += f"<li>{priority} {act}{goal_text}</li>"
                else:
                    actions_html += f"<li>{a}</li>"
            actions_html += "</ul>"

        # Red flags
        red_flags = r.get("red_flags", [])
        red_flags_html = ""
        if red_flags:
            red_flags_html = '<div style="margin:4px 0;">' + " ".join(
                f'<span style="display:inline-block;padding:1px 6px;border-radius:3px;background:#fef2f2;color:#dc2626;font-size:11px;margin-right:4px;">{flag}</span>'
                for flag in red_flags
            ) + "</div>"

        # Assessment & stage
        assessment = r.get("overall_assessment", "")
        level_reason = r.get("level_reason", "")
        sales_stage = r.get("sales_stage", "")
        stage_html = f'<span style="display:inline-block;padding:2px 6px;border-radius:3px;background:#eff6ff;color:#2563eb;font-size:11px;margin-left:6px;">{sales_stage}</span>' if sales_stage else ""

        order_status = r.get("order_status", "")
        if order_status == "已下单":
            order_badge = '<span style="display:inline-block;padding:2px 6px;border-radius:3px;background:#059669;color:#fff;font-size:11px;margin-left:6px;">已下单</span>'
        else:
            order_badge = ""

        blarge_attr = "true" if is_bl else "false"
        type_label = "B端大客户" if is_bl else ctype

        cards.append(f"""
<div class="customer-card {level_class}" data-level="{level}" data-type="{ctype}" data-blarge="{blarge_attr}">
  <div class="customer-header">
    <span class="name">{name} <span style="color:#9ca3af;font-size:12px;font-weight:400;">({msg_count}条消息)</span>{stage_html}</span>
    <span class="badges">
      {_level_badge(level)}
      {_type_badge(type_label)}
      {f'<span style="display:inline-block;padding:2px 8px;border-radius:4px;background:#fbbf24;color:#000;font-size:12px;font-weight:600;">B端大客户</span>' if is_bl else ""}
      {order_badge}
    </span>
  </div>
  {red_flags_html}
  {info_html}
  {"<div style='font-size:13px;color:#6b7280;margin:4px 0;'>定级理由: " + level_reason + "</div>" if level_reason else ""}
  {issues_html}
  {actions_html}
  {"<div class='assessment'>" + assessment + "</div>" if assessment else ""}
</div>""")

    return "\n".join(cards)


def _markdown_to_html(text: str) -> str:
    """Simple markdown-to-HTML conversion for the summary section."""
    lines = text.split("\n")
    out = []
    in_list = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("## "):
            if in_list:
                out.append("</ul>")
                in_list = False
            out.append(f"<h2>{stripped[3:]}</h2>")
        elif stripped.startswith("### "):
            if in_list:
                out.append("</ul>")
                in_list = False
            out.append(f"<h3>{stripped[4:]}</h3>")
        elif stripped.startswith("- "):
            if not in_list:
                out.append("<ul>")
                in_list = True
            out.append(f"<li>{stripped[2:]}</li>")
        elif stripped.startswith("1. ") or (stripped and stripped[0].isdigit() and ". " in stripped[:4]):
            content = stripped.split(". ", 1)[-1] if ". " in stripped else stripped
            out.append(f"<p>{stripped}</p>")
        elif stripped:
            if in_list:
                out.append("</ul>")
                in_list = False
            out.append(f"<p>{stripped}</p>")
        else:
            if in_list:
                out.append("</ul>")
                in_list = False
            out.append("<br>")
    if in_list:
        out.append("</ul>")
    return "\n".join(out)


def save_report(html: str, agent_name: str, output_dir: str = "output") -> str:
    """Save HTML report to file."""
    os.makedirs(output_dir, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"{agent_name}_{timestamp}.html"
    filepath = os.path.join(output_dir, filename)
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(html)
    return filepath


def save_json_results(results: list, agent_name: str, output_dir: str = "output") -> str:
    """Save raw analysis results as JSON."""
    os.makedirs(output_dir, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"{agent_name}_{timestamp}.json"
    filepath = os.path.join(output_dir, filename)
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    return filepath


def generate_csv(results: list) -> str:
    """Generate CSV table of diagnosis results (Excel-compatible with BOM)."""
    import csv
    import io

    output = io.StringIO()
    writer = csv.writer(output)

    # Header
    headers = [
        "序号", "客户名称", "客户等级", "客户类型", "B端大客户", "订单状态",
        "销售阶段", "风险信号",
        "定级理由", "产品意向", "需求量", "预算", "时间节点", "痛点/关注点",
        "决策链", "竞品信息",
        "沟通问题(高)", "沟通问题(中)", "沟通问题(低)",
        "跟进建议", "整体评价", "备注"
    ]
    writer.writerow(headers)

    for i, r in enumerate(results, 1):
        if r.get("error"):
            continue

        key = r.get("key_info", {}) or {}
        issues = r.get("communication_issues", []) or []

        high_issues = "; ".join(
            f"{iss.get('issue','')}({iss.get('example','')})"
            for iss in issues if iss.get("severity") == "高"
        )
        mid_issues = "; ".join(
            f"{iss.get('issue','')}({iss.get('example','')})"
            for iss in issues if iss.get("severity") == "中"
        )
        low_issues = "; ".join(
            f"{iss.get('issue','')}({iss.get('example','')})"
            for iss in issues if iss.get("severity") == "低"
        )

        actions = r.get("follow_up_actions", []) or []
        if actions and isinstance(actions[0], dict):
            actions_text = "; ".join(
                f"{a.get('priority','')} {a.get('action','')} [{a.get('commitment_goal','')}]"
                for a in actions
            )
        else:
            actions_text = "; ".join(actions)

        red_flags = r.get("red_flags", []) or []
        red_flags_text = "; ".join(red_flags)

        row = [
            i,
            r.get("customer_name", ""),
            r.get("customer_level", ""),
            r.get("customer_type", ""),
            "是" if r.get("is_b_large") else "否",
            r.get("order_status", ""),
            r.get("sales_stage", ""),
            red_flags_text,
            r.get("level_reason", ""),
            key.get("product_interest", ""),
            key.get("quantity", ""),
            key.get("budget", ""),
            key.get("timeline", ""),
            key.get("pain_points", ""),
            key.get("decision_chain", ""),
            key.get("competitor_mention", ""),
            high_issues,
            mid_issues,
            low_issues,
            actions_text,
            r.get("overall_assessment", ""),
            r.get("_notes", ""),
        ]
        writer.writerow(row)

    return output.getvalue()


def generate_key_followup_html(results: list, agent_name: str) -> str:
    """Generate a clean key-customer follow-up table (S+A level only, excluding ordered customers)."""
    key_customers = [r for r in results
                     if r.get("customer_level") in ("S", "A")
                     and not r.get("error")
                     and r.get("order_status") != "已下单"]

    if not key_customers:
        return f"""<!DOCTYPE html><html lang="zh-CN"><head><meta charset="UTF-8">
<title>重点客户跟进表 - {agent_name}</title></head>
<body style="font-family:PingFang SC,Microsoft YaHei,sans-serif;padding:48px;text-align:center;">
<h1 style="color:#1e3a5f;">重点客户跟进表</h1><p>客服：{agent_name}</p>
<p style="color:#9ca3af;margin-top:40px;">暂无 S/A 级待跟进客户。<br>（已下单客户已自动排除，无需跟进）</p>
</body></html>"""

    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    s_count = sum(1 for r in key_customers if r.get("customer_level") == "S")
    a_count = sum(1 for r in key_customers if r.get("customer_level") == "A")

    rows_html = ""
    for idx, r in enumerate(key_customers, 1):
        level = r.get("customer_level", "")
        level_color = "#dc2626" if level == "S" else "#ea580c"
        ctype = r.get("customer_type", "")
        is_bl = r.get("is_b_large", False)
        stage = r.get("sales_stage", "")

        red_flags = r.get("red_flags", [])
        flags_html = ""
        if red_flags:
            flags_html = '<div style="margin:6px 0;">' + " ".join(
                f'<span style="display:inline-block;padding:2px 8px;border-radius:3px;background:#fef2f2;color:#dc2626;font-size:11px;margin:2px;">{flag}</span>'
                for flag in red_flags
            ) + "</div>"

        key = r.get("key_info", {})
        info_parts = []
        if key.get("product_interest"):
            info_parts.append(f"产品：{key['product_interest']}")
        if key.get("quantity"):
            info_parts.append(f"数量：{key['quantity']}")
        if key.get("budget"):
            info_parts.append(f"预算：{key['budget']}")
        if key.get("timeline"):
            info_parts.append(f"时间：{key['timeline']}")
        if key.get("pain_points"):
            info_parts.append(f"痛点：{key['pain_points']}")
        if key.get("competitor_mention"):
            info_parts.append(f"竞品：{key['competitor_mention']}")
        info_line = " | ".join(info_parts)

        issues = r.get("communication_issues", [])
        issues_html = ""
        if issues:
            issues_html = '<table style="width:100%;border-collapse:collapse;font-size:12px;margin:6px 0;"><tbody>'
            for iss in issues[:5]:
                sev = iss.get("severity", "")
                sev_c = {"高": "#dc2626", "中": "#ea580c", "低": "#6b7280"}.get(sev, "#6b7280")
                fix = iss.get("fix", "")
                fix_td = f'<td style="padding:4px 8px;border-bottom:1px solid #f0f0f0;color:#2563eb;font-size:11px;">{fix}</td>' if fix else '<td></td>'
                issues_html += f"""<tr>
<td style="padding:4px 8px;border-bottom:1px solid #f0f0f0;width:18px;"><span style="color:{sev_c};font-weight:700;">{sev}</span></td>
<td style="padding:4px 8px;border-bottom:1px solid #f0f0f0;">{iss.get('issue','')}</td>
{fix_td}
</tr>"""
            issues_html += "</tbody></table>"

        actions = r.get("follow_up_actions", [])
        actions_html = ""
        if actions:
            actions_html = '<ol style="margin:4px 0;padding-left:18px;font-size:12px;">'
            for a in actions[:5]:
                if isinstance(a, dict):
                    p = a.get("priority", "")
                    act = a.get("action", "")
                    goal = a.get("commitment_goal", "")
                    actions_html += f'<li style="margin:4px 0;line-height:1.5;"><span style="margin-right:4px;">{p}</span>{act}<br><span style="color:#2563eb;font-size:11px;">目标：{goal}</span></li>'
                else:
                    actions_html += f'<li style="margin:4px 0;line-height:1.5;">{a}</li>'
            actions_html += "</ol>"

        tag_html = ""
        if is_bl:
            tag_html += '<span style="display:inline-block;padding:2px 6px;border-radius:3px;background:#fbbf24;color:#000;font-size:11px;font-weight:600;margin-left:6px;">大客户</span>'
        if stage:
            tag_html += f'<span style="display:inline-block;padding:2px 6px;border-radius:3px;background:#eff6ff;color:#2563eb;font-size:11px;margin-left:6px;">{stage}</span>'

        rows_html += f"""
<div style="border:1px solid #e5e7eb;border-left:4px solid {level_color};border-radius:6px;padding:14px;margin-bottom:14px;page-break-inside:avoid;">
  <div style="display:flex;justify-content:space-between;align-items:flex-start;flex-wrap:wrap;gap:8px;">
    <div>
      <span style="font-size:15px;font-weight:700;">{idx}. {r.get('customer_name','?')}</span>
      <span style="display:inline-block;padding:2px 8px;border-radius:10px;background:{level_color};color:#fff;font-size:11px;font-weight:700;margin-left:8px;">{level}</span>
      <span style="display:inline-block;padding:2px 6px;border-radius:3px;background:#7c3aed;color:#fff;font-size:11px;margin-left:4px;">{ctype}</span>
      {tag_html}
    </div>
    <div style="font-size:11px;color:#6b7280;">{r.get('_msg_count','')} 条消息</div>
  </div>
  {flags_html}
  {f'<div style="font-size:12px;color:#6b7280;margin:6px 0;"><strong>关键信息：</strong>{info_line}</div>' if info_line else ''}
  {f'<div style="font-size:12px;color:#6b7280;margin:4px 0;">定级理由：{r.get("level_reason","")}</div>' if r.get("level_reason") else ''}

  <div style="margin:8px 0;">
    <div style="font-size:12px;font-weight:600;color:#374151;margin-bottom:4px;">沟通问题诊断</div>
    {issues_html if issues_html else '<div style="font-size:12px;color:#9ca3af;">无明显问题</div>'}
  </div>

  <div style="margin:8px 0;">
    <div style="font-size:12px;font-weight:600;color:#374151;margin-bottom:4px;">跟进计划</div>
    {actions_html if actions_html else '<div style="font-size:12px;color:#9ca3af;">待制定</div>'}
  </div>

  {f'<div style="font-size:11px;color:#9ca3af;margin-top:8px;border-top:1px solid #f0f0f0;padding-top:6px;">判断：{r.get("overall_assessment","")}</div>' if r.get("overall_assessment") else ''}
</div>"""

    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>重点客户跟进表 - {agent_name}</title>
<style>
* {{ margin: 0; padding: 0; box-sizing: border-box; }}
body {{ font-family: -apple-system, BlinkMacSystemFont, "PingFang SC", "Microsoft YaHei", sans-serif; background: #f5f5f5; color: #1f2937; line-height: 1.6; }}
.container {{ max-width: 900px; margin: 0 auto; padding: 24px; }}
.cover {{ background: linear-gradient(135deg, #1e3a5f, #2563eb); color: #fff; padding: 28px 32px; border-radius: 10px; margin-bottom: 20px; }}
.cover h1 {{ font-size: 20px; margin-bottom: 4px; }}
.cover .meta {{ font-size: 12px; opacity: 0.8; }}
.stats {{ display: flex; gap: 10px; margin-bottom: 20px; }}
.stat {{ flex: 1; background: #fff; border-radius: 8px; padding: 14px; text-align: center; box-shadow: 0 1px 2px rgba(0,0,0,0.04); }}
.stat .num {{ font-size: 26px; font-weight: 700; }}
.stat .lbl {{ font-size: 11px; color: #6b7280; }}
.stat.s .num {{ color: #dc2626; }}
.stat.a .num {{ color: #ea580c; }}
@media print {{
  body {{ background: #fff; }}
  .container {{ max-width: 100%; padding: 0; }}
  .cover {{ border-radius: 0; }}
}}
</style>
</head>
<body>
<div class="container">

<div class="cover">
  <h1>重点客户跟进表</h1>
  <div class="meta">客服：{agent_name} | 生成时间：{now} | 共 {len(key_customers)} 个重点客户（S级 {s_count} · A级 {a_count}）</div>
</div>

<div class="stats">
  <div class="stat s"><div class="num">{s_count}</div><div class="lbl">S级 · 紧急</div></div>
  <div class="stat a"><div class="num">{a_count}</div><div class="lbl">A级 · 重点</div></div>
  <div class="stat"><div class="num">{len(key_customers)}</div><div class="lbl">总计</div></div>
  <div class="stat"><div class="num">{sum(1 for r in key_customers if r.get("is_b_large"))}</div><div class="lbl">B端大客户</div></div>
</div>

{rows_html}

</div>
</body>
</html>"""
    return html


def save_csv(results: list, agent_name: str, output_dir: str = "output") -> str:
    """Save diagnosis table as CSV file (Excel-compatible UTF-8 BOM)."""
    os.makedirs(output_dir, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"{agent_name}_{timestamp}.csv"
    filepath = os.path.join(output_dir, filename)
    csv_content = generate_csv(results)
    with open(filepath, "w", encoding="utf-8-sig") as f:
        f.write(csv_content)
    return filepath
