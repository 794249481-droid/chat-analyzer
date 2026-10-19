Warning: fs was declared with const; use let for reassignable variables.
#!/usr/bin/env python3
"""销售客服分析诊断系统 - CLI入口

支持两种输入模式：
  1. JSON 文件:   python3 main.py chat_export.json
  2. 聊天截图目录: python3 main.py 客户聊天记录汇总/
  3. 单客户目录:   python3 main.py 客户聊天记录汇总/客服甲--客户乙-聊了15页/

Usage:
  python3 main.py <input>                 # 全量分析
  python3 main.py <input> --limit 5       # 限制分析数量
  python3 main.py <input> --output-dir my_reports/
"""

import argparse
import sys
import os
from pathlib import Path

from parser import parse_json_export, parse_chat_folders, parse_folder_name, find_images
from analyzer import (
    batch_analyze_json,
    batch_analyze_images,
    analyze_conversation_images,
    generate_summary,
)
from reporter import generate_html, save_report, save_json_results, save_csv


def main():
    parser = argparse.ArgumentParser(
        description="销售客服分析诊断系统 - 诊断沟通问题、客户分级、跟进建议"
    )
    parser.add_argument("input", help="JSON聊天导出文件 或 聊天记录目录路径")
    parser.add_argument("--limit", type=int, default=0, help="限制分析数量（0=全部）")
    parser.add_argument("--min-msg", type=int, default=2, help="JSON模式: 最少消息数（默认2）")
    parser.add_argument("--output-dir", default="output", help="报告输出目录（默认output）")
    parser.add_argument("--no-summary", action="store_true", help="不生成整体总结")
    parser.add_argument("--mode", choices=["json", "images", "auto"], default="auto",
                        help="输入模式（默认自动检测）")
    args = parser.parse_args()

    input_path = Path(args.input)
    if not input_path.exists():
        print(f"错误: 路径不存在 - {args.input}")
        sys.exit(1)

    print("=" * 60)
    print("  销售客服分析诊断系统")
    print("=" * 60)

    # ── Detect mode ────────────────────────────────────────────────
    mode = args.mode
    if mode == "auto":
        if input_path.is_file() and input_path.suffix == ".json":
            mode = "json"
        elif input_path.is_dir():
            # Check if this is a single customer folder or a root folder
            has_images = len(find_images(str(input_path))) > 0
            subdirs = [d for d in input_path.iterdir() if d.is_dir()]
            if has_images and not subdirs:
                mode = "single_image"
            else:
                mode = "images"
        else:
            print("错误: 无法识别输入类型。请提供JSON文件或聊天截图目录。")
            sys.exit(1)

    print(f"  模式: {mode}")
    output_dir = str(Path(__file__).parent / args.output_dir)

    # ── Single image folder mode ───────────────────────────────────
    if mode == "single_image":
        info = parse_folder_name(input_path.name)
        agent_name = info.get("agent_name", "Unknown")
        customer_name = info.get("customer_name", input_path.name)
        images = find_images(str(input_path))

        print(f"\n  客服: {agent_name}")
        print(f"  客户: {customer_name}")
        print(f"  截图: {len(images)} 张")

        print(f"\n[分析中]...")
        result = analyze_conversation_images(images, customer_name, agent_name)
        result["_notes"] = info.get("notes", "")
        results = [result]

        summary = generate_summary(results, agent_name) if not args.no_summary else ""

        html = generate_html(results, summary, agent_name, {"source": "image_folder"})
        report_path = save_report(html, agent_name, output_dir)
        json_path = save_json_results(results, agent_name, output_dir)
        csv_path = save_csv(results, agent_name, output_dir)

        print(f"\n报告已生成:")
        print(f"  HTML: {report_path}")
        print(f"  CSV:  {csv_path}")
        print(f"  JSON: {json_path}")
        return

    # ── JSON mode ──────────────────────────────────────────────────
    if mode == "json":
        print(f"\n[1/4] 解析JSON: {input_path.name}")
        parsed = parse_json_export(str(input_path))
        agent_name = parsed["agent_name"]
        conversations = parsed["conversations"]
        print(f"  客服: {agent_name}")
        print(f"  客户数: {len(conversations)}")

        # Filter
        valid = {k: v for k, v in conversations.items() if len(v) >= args.min_msg}
        print(f"  有效客户: {len(valid)} (跳过 {len(conversations) - len(valid)} 个)")

        if args.limit and args.limit < len(valid):
            keys = list(valid.keys())[:args.limit]
            valid = {k: valid[k] for k in keys}
            print(f"  限制: {args.limit} 个")

        print(f"\n[2/4] AI分析中...")
        results = batch_analyze_json(valid, agent_name, min_messages=args.min_msg)

    # ── Images folder mode ─────────────────────────────────────────
    elif mode == "images":
        print(f"\n[1/4] 扫描目录: {input_path.name}")
        parsed = parse_chat_folders(str(input_path))
        agent_name = parsed["agent_name"]
        conversations = parsed["conversations"]
        print(f"  客服: {agent_name}")
        print(f"  客户数: {len(conversations)}")

        if args.limit and args.limit < len(conversations):
            keys = list(conversations.keys())[:args.limit]
            conversations = {k: conversations[k] for k in keys}
            print(f"  限制: {args.limit} 个")

        print(f"\n[2/4] AI转录+分析中...")
        results = batch_analyze_images(conversations, agent_name)

    else:
        print(f"错误: 未知模式 {mode}")
        sys.exit(1)

    # ── Summary ────────────────────────────────────────────────────
    error_count = sum(1 for r in results if r.get("error"))
    success_count = len(results) - error_count
    print(f"\n  分析完成: 成功 {success_count}, 失败 {error_count}")

    summary = ""
    if not args.no_summary and success_count > 0:
        print(f"\n[3/4] 生成整体总结...")
        summary = generate_summary(results, agent_name)
        print("  总结完成")

    # ── Report ─────────────────────────────────────────────────────
    print(f"\n[4/4] 生成报告...")
    metadata = parsed.get("metadata", {"source": "images"})
    html = generate_html(results, summary, agent_name, metadata)

    report_path = save_report(html, agent_name, output_dir)
    json_path = save_json_results(results, agent_name, output_dir)
    csv_path = save_csv(results, agent_name, output_dir)

    print(f"\n{'=' * 60}")
    print(f"  报告生成完成!")
    print(f"  HTML: {report_path}")
    print(f"  CSV:  {csv_path}")
    print(f"  JSON: {json_path}")
    print(f"{'=' * 60}")

    s_count = sum(1 for r in results if r.get("customer_level") == "S")
    a_count = sum(1 for r in results if r.get("customer_level") == "A")
    b_large = sum(1 for r in results if r.get("is_b_large"))

    print(f"\n  快速统计:")
    print(f"    S级(紧急): {s_count} | A级(重点): {a_count}")
    print(f"    B端大客户: {b_large}")

    if s_count > 0:
        print(f"\n  S级客户:")
        for r in results:
            if r.get("customer_level") == "S":
                print(f"    - {r.get('customer_name')}")


if __name__ == "__main__":
    main()
