Warning: fs was declared with const; use let for reassignable variables.
"""AI analysis module using DeepSeek API for chat diagnosis."""

import json
import os
import subprocess
import requests
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY", "").strip()
DEEPSEEK_BASE_URL = "https://api.deepseek.com/v1"
DEEPSEEK_MODEL = "deepseek-chat"
PARALLEL_WORKERS = 8

# Local OCR tool path (Swift + macOS Vision framework)
_OCR_TOOL = Path(__file__).parent / "ocr_tool"

ANALYSIS_SYSTEM_PROMPT = """你是基于钟德财销售脑方法论驱动的AI销售诊断专家。你有17年B2B销售实战经验，擅长从聊天记录中诊断销售客服的沟通问题并给出可落地的改进方案。

你的核心原则——「四有标准」：
1. 有判断：值得跟/不值得/赶紧逼单/先放一放/大胆冲
2. 有方案：不说"加强沟通"，说"今天下午3点前打电话，这样说..."
3. 有话术：用「」包裹可直接复制的话术
4. 有下一步：做完这一步后，下一句怎么说/下一个动作是什么

分析维度：

1. **沟通问题诊断**（从以下专业维度诊断，问题描述要具体到卡点类型）：
   - 客户开发类：开场白话术生硬（类似"在吗"/"您好"无效开场）、未建立信任就推销、加微信后无有效激活、被动等待客户主动
   - 需求挖掘类：未使用SPIN提问法（不问背景/困难/影响/需求）、只问封闭式问题、未识别隐性需求、未探知预算和时间节点
   - 价值呈现类：只会报价不会讲价值、未做FABE差异化呈现、未用案例/证据支撑、方案与客户痛点脱节
   - 谈判成交类：被"太贵了"牵制未拆解价值、未设定承诺目标、面对"再考虑考虑"无逼单策略、未识别"考虑"背后的真实原因（体面拒绝/真实犹豫/隐藏顾虑/权力游戏）
   - 客户经营类：无有效跟进节奏（报价后黄金跟单期内未推进）、每次跟进都是"在吗/进展怎样"索取式沟通而非价值式触达
   - 心态/态度类：响应速度慢、语气生硬敷衍、缺乏主动服务意识

2. **客户意向等级**（S/A/B/C 四级，结合黄金跟单期理论）：
   - S级：明确有批采/定制/工程需求，询价后积极互动，已进入比价或决策阶段，决策窗口<14天 → 需立即逼单
   - A级：有明确需求、沟通积极、多次互动，但仍在了解方案阶段，决策窗口14-30天 → 需加速推进
   - B级：有需求方向但沟通不深，或初次询价后回复不积极，决策窗口>30天 → 需持续价值触达
   - C级：随便问问/比价/闲聊/试探性询价，无明确时间节点 → 定期维护即可

3. **客户类型判定**（严格按以下标准，只分两类）：

   **重要：B端判定极其严格，必须同时满足「场景关键词」+「需求量≥10张/件/套」，缺一不可！**

   **B端场景关键词（只有这些，其他一律不算）**：
   - 酒店类：酒店、民宿、宾馆、客栈、度假村
   - 外贸类：外贸、出口、跨境、跨境电商、OEM、ODM、贴牌
   - 工程项目类：工程、工地、项目、投标、招标、甲方、连锁、学校、医院、养老院

   **判定规则（三步法）**：
   第一步 — 检查场景关键词：聊天中是否出现上述B端场景关键词？
   第二步 — 检查需求量：需求量是否明确 ≥ 10 张/件/套？
   第三步 — 两步都满足 → 「B端」；任一步不满足 → 「家用」

   **一律归「家用」的情况**：
   - 需求量 < 10，即使提到酒店/外贸等关键词也归家用（小批量零售不算B端）
   - 需求量不明确/未知 → 默认家用
   - 产品名称中含"酒店/民宿"字样但客户是个人自用 → 家用
   - 电商/网店/直播带货等个人经营 → 家用
   - 批发/经销商/代理/定制等词出现但需求量<10 → 家用（小批发不是B端）
   - 办公室/员工宿舍/健身房/美容院等出现但需求量<10 → 家用
   - 企业/公司采购但数量<10 → 家用

   **边界情况处理**：
   - 信息不足无法判断需求量 → 默认"家用"
   - 同时存在B端场景词和家用特征 → 以需求量为准，<10一律家用
   - 客户身份不明但需求量≥10且有B端场景词 → B端

4. **B端大客户标记**：B端客户且满足以下任一条件 → true：
   - 需求量 ≥ 50 张/件/套
   - 明确提到：工程/外贸/出口/跨境/连锁酒店/投标/OEM/ODM/贴牌/长期合作
   - 需求量≥20且明确是酒店/工程项目/出口订单

5. **销售阶段判定**（严格只有以下4个阶段，根据聊天记录证据判定）：

   **「已经成交」** — 满足以下任一信号：
   - 订单确认：提到"已拍""已下单""订单号""付款了""已支付""拍了""成交""下单了"
   - 催货/物流：询问发货进度、催货、要单号、查物流、"什么时候发货""货到哪了"
   - 感谢/好评：客服说"感谢支持""谢谢惠顾""已安排发货""已经发出"，客户说"收到了""好评""满意"
   - 出现订单金额、运费确认、地址确认等成交后环节

   **「售后中」** — 满足以下任一信号：
   - 客户提出退换货、退款、质量问题、发错货、少货
   - 客户抱怨产品问题，需要售后处理
   - 客服在处理售后工单、补偿方案

   **「已经加微信」** — 满足以下任一信号：
   - 客户或客服提到"已经加您了""加了微信""微信聊""加一下微信"
   - 聊天中明确出现微信号交换且确认已添加
   - 注意：仅提到"加微信吧"但未确认添加的，不算已加微信

   **「正在谈判」** — 以上3种都不满足时，默认归入此阶段：
   - 正在询价、议价、讨论方案、比价
   - 客户在了解产品但未成交
   - 任何其他非成交/非售后/非加微信的沟通状态

6. **跟进建议**（3-5条，每条必须含"承诺目标"）：
   每条建议包含：(1)具体动作+时间节点 (2)预期承诺目标（如"拿到尺寸参数""约到电话沟通""确认预算区间"）(3)遇到阻力时的备用策略
   按优先级排序，标注🔴紧急/🟡本周/🟢持续

7. **关键信息提取**：产品需求/数量/预算/时间节点/决策人/痛点/竞品信息

8. **已下单判定**：综合以下信号判断客户是否已下单（标注 `order_status`）：
   - ✅ 直接下单信号：聊天中提到"已拍""已下单""订单号""付款了""已支付""拍了"等
   - ✅ 催货/物流信号：询问发货进度、催货、要单号、查物流、"什么时候发货""货到哪了"
   - ✅ 售后/收货信号："收到了""收到了谢谢""感谢支持""好评""满意""再来""复购"
   - ✅ 客服确认信号：客服说"感谢支持""谢谢惠顾""已安排发货""已经发出"
   - ❌ 仅询价/咨询/比价，未出现上述信号 → 未下单

   **重要**：已下单客户 (`order_status: "已下单"`) **不出现在重点客户跟进表中**，仅在完整报告中展示。
   S/A/B/C 等级仍然正常评定，但重点跟进表过滤掉已下单客户。

JSON输出格式（只输出JSON，不要其他内容）：
{
  "customer_name": "客户名",
  "customer_level": "S/A/B/C",
  "customer_type": "B端/家用",
  "is_b_large": true/false,
  "order_status": "已下单/未下单",
  "order_evidence": "判定依据（如：提到已付款/催发货/已收到货等）",
  "level_reason": "定级理由（含决策窗口判断）",
  "sales_stage": "当前销售阶段（正在谈判/已经加微信/已经成交/售后中）",
  "wechat_evidence": "加微信判定依据（如果有）",
  "red_flags": ["风险信号1", "风险信号2"],
  "communication_issues": [
    {"issue": "问题描述（含对应卡点类型）", "severity": "高/中/低", "example": "聊天记录中的具体原文", "fix": "具体改进话术（用「」包裹）"}
  ],
  "key_info": {
    "product_interest": "感兴趣的产品/品类",
    "quantity": "需求量",
    "budget": "预算",
    "timeline": "时间节点",
    "pain_points": "痛点/关注点",
    "decision_chain": "已知决策人/影响者信息",
    "competitor_mention": "提及的竞品信息"
  },
  "follow_up_actions": [
    {"action": "具体动作+话术", "commitment_goal": "承诺目标", "priority": "🔴/🟡/🟢", "fallback": "如遇阻力备用方案"}
  ],
  "overall_assessment": "整体评价2-3句话，必须有明确判断（值得跟/快逼单/先放着/放弃）"
}"""


def _call_deepseek(system_prompt: str, user_message: str, temperature: float = 0.3) -> str:
    """Call DeepSeek Chat Completion API."""
    if not DEEPSEEK_API_KEY:
        raise RuntimeError("请先设置 DEEPSEEK_API_KEY 环境变量")
    headers = {
        "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
        "Content-Type": "application/json",
    }

    payload = {
        "model": DEEPSEEK_MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ],
        "temperature": temperature,
        "max_tokens": 4096,
    }
    resp = requests.post(
        f"{DEEPSEEK_BASE_URL}/chat/completions",
        headers=headers,
        json=payload,
        timeout=180,
    )
    resp.raise_for_status()
    data = resp.json()
    return data["choices"][0]["message"]["content"]


def transcribe_images(image_paths: list) -> str:
    """Transcribe chat screenshots to text using macOS Vision OCR."""
    if not _OCR_TOOL.exists():
        return f"[OCR工具未找到: {_OCR_TOOL}]"

    all_text = []
    for i, path in enumerate(image_paths):
        try:
            result = subprocess.run(
                [str(_OCR_TOOL), path],
                capture_output=True, text=True, timeout=120,
            )
            if result.returncode == 0 and result.stdout.strip():
                all_text.append(f"--- 第{i+1}张截图 ---\n{result.stdout.strip()}")
            else:
                all_text.append(f"--- 第{i+1}张截图 ---\n[OCR失败: {result.stderr}]")
        except Exception as e:
            all_text.append(f"--- 第{i+1}张截图 ---\n[OCR异常: {e}]")

    # Use DeepSeek to clean and structure the OCR output
    transcript = "\n\n".join(all_text)
    if len(transcript) > 500:
        transcript = _clean_transcript_with_ai(transcript)
    return transcript


def _clean_transcript_with_ai(raw_ocr: str) -> str:
    """Use DeepSeek to clean up OCR output into structured chat transcript."""
    prompt = f"""请将以下OCR识别出的1688聊天记录整理成清晰的对话格式。

要求：
1. 区分每条消息的发送者：客服、客户、系统消息
2. 保留时间戳（如果有）
3. 格式：[时间] 角色: 消息内容
4. 合并重复或断开的OCR行
5. 保持对话顺序

OCR原始内容：
---
{raw_ocr}
---"""

    try:
        return _call_deepseek("You are a helpful text formatting assistant.", prompt, temperature=0.1)
    except Exception:
        return raw_ocr


def _extract_json(result: str) -> dict:
    """Extract JSON object from model response."""
    json_start = result.find("{")
    json_end = result.rfind("}") + 1
    if json_start >= 0 and json_end > json_start:
        try:
            return json.loads(result[json_start:json_end])
        except json.JSONDecodeError:
            pass
    return {"error": "parse_failed", "raw": result}


def analyze_conversation_text(transcript: str, customer_name: str, agent_name: str) -> dict:
    """Analyze a text transcript of a customer conversation."""
    user_message = f"""请分析以下销售客服与客户的聊天记录。

客服: {agent_name}
客户: {customer_name}

聊天记录：
---
{transcript}
---"""

    try:
        result = _call_deepseek(ANALYSIS_SYSTEM_PROMPT, user_message)
        return _extract_json(result)
    except Exception as e:
        return {"error": str(e)}


def analyze_conversation_images(image_paths: list, customer_name: str, agent_name: str) -> dict:
    """Transcribe images then analyze the conversation."""
    print(f"    转录 {len(image_paths)} 张聊天截图...")
    transcript = transcribe_images(image_paths)

    if transcript.startswith("[转录失败"):
        return {"error": transcript}

    print(f"    分析转录内容 ({len(transcript)} 字符)...")
    return analyze_conversation_text(transcript, customer_name, agent_name)


# ── Batch analysis ─────────────────────────────────────────────────

def batch_analyze_json(conversations: dict, agent_name: str, min_messages: int = 3,
                       progress_callback=None, max_items: int = 0) -> list:
    """Analyze JSON-parsed conversations with parallel API calls.

    Args:
        progress_callback: fn(current, total, customer_name) called after each analysis
        max_items: if > 0, limit to this many customers
    """
    valid = [(k, v) for k, v in conversations.items() if len(v) >= min_messages]
    if max_items and max_items < len(valid):
        valid = valid[:max_items]

    total = len(valid)
    results = []
    completed = 0

    def analyze_one(cust_name, msgs):
        from parser import conversation_to_text
        transcript = conversation_to_text(msgs, agent_name)
        result = analyze_conversation_text(transcript, cust_name, agent_name)
        result["_msg_count"] = len(msgs)
        return result

    with ThreadPoolExecutor(max_workers=PARALLEL_WORKERS) as executor:
        futures = {executor.submit(analyze_one, name, msgs): name for name, msgs in valid}
        for future in as_completed(futures):
            name = futures[future]
            try:
                result = future.result()
                results.append(result)
            except Exception as e:
                results.append({"customer_name": name, "error": str(e)})
            completed += 1
            print(f"  [{completed}/{total}] 分析: {name}")
            if progress_callback:
                progress_callback(completed, total, name)

    return _sort_results(results)


def batch_analyze_images(conversations: dict, agent_name: str,
                         progress_callback=None, max_items: int = 0) -> list:
    """Analyze image-based conversations with parallel processing.

    Args:
        progress_callback: fn(current, total, customer_name) called after each analysis
        max_items: if > 0, limit to this many customers
    """
    valid = [(k, v) for k, v in conversations.items() if v.get("images")]
    if max_items and max_items < len(valid):
        valid = valid[:max_items]

    total = len(valid)
    results = []
    completed = 0

    def analyze_one(cust_name, info):
        images = info.get("images", [])
        result = analyze_conversation_images(images, cust_name, info.get("agent_name", agent_name))
        result["_msg_count"] = len(images)
        result["_notes"] = info.get("notes", "")
        if "customer_name" not in result or not result["customer_name"]:
            result["customer_name"] = cust_name
        return result

    with ThreadPoolExecutor(max_workers=PARALLEL_WORKERS) as executor:
        futures = {executor.submit(analyze_one, name, info): name for name, info in valid}
        for future in as_completed(futures):
            name = futures[future]
            try:
                result = future.result()
                results.append(result)
            except Exception as e:
                results.append({"customer_name": name, "error": str(e)})
            completed += 1
            print(f"  [{completed}/{total}] 分析: {name}")
            if progress_callback:
                progress_callback(completed, total, name)

    return _sort_results(results)


def _sort_results(results: list) -> list:
    level_order = {"S": 0, "A": 1, "B": 2, "C": 3}
    results.sort(key=lambda r: level_order.get(r.get("customer_level", "C"), 99))
    return results


# ── Summary generation ─────────────────────────────────────────────

def generate_summary(results: list, agent_name: str) -> str:
    """Generate an overall summary analysis of the agent's performance."""
    system_prompt = """你是基于钟德财销售脑方法论的销售团队绩效分析师。根据多条客户聊天记录的诊断结果，生成客服整体表现总结。

分析维度：
1. 客户等级分布（S/A/B/C占比），S/A级客户是否被及时跟进
2. B端 vs C端客户占比，大客户是否被识别并重点对待
3. 客服共性问题——按钟德财销售脑的六大阶段分类（客户开发/需求挖掘/价值呈现/谈判成交/客户经营/心态），指出最突出的3-5个卡点类型
4. 整体改进建议——按优先级排序，每条建议含具体训练方向（如"练习SPIN提问法""建立FABE话术库""执行黄金跟单期SOP"）
5. 当前最需要紧急跟进的客户（S级客户列表+建议动作+承诺目标）
6. 红绿灯预警：🔴流失风险客户 / 🟡拖延风险客户 / 🟢健康推进客户

输出Markdown格式，要求有判断、有方案、有话术、有下一步。"""

    user_message = json.dumps(results, ensure_ascii=False, indent=2)
    if len(user_message) > 30000:
        user_message = user_message[:30000]

    try:
        return _call_deepseek(system_prompt, user_message, temperature=0.5)
    except Exception as e:
        return f"总结生成失败: {e}"
