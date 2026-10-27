# 销售聊天分析器 · Sales Chat Analyzer

> 在本地分析 JSON 聊天导出或聊天截图，生成客户分级、沟通诊断、跟进建议和 HTML/CSV/JSON 报告。
>
> Analyze exported sales chats locally and generate lead scoring, conversation diagnosis, follow-up suggestions, and HTML/CSV/JSON reports.

**本地运行 · 支持 JSON · 可选 OCR · 多格式报告 · 不内置客户数据**

## 适合谁

- 销售复盘客户沟通质量
- 销售主管检查跟进优先级和常见卡点
- 需要在本机处理聊天数据、减少上传外部平台的团队

## 60 秒开始

一个本地运行的聊天记录分析工具：读取 JSON 导出或聊天截图，生成客户分级、沟通诊断、跟进建议以及 HTML、CSV、JSON 报告。它适合用自己的测试数据做分析，不包含历史客户记录。

```sh
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
python3 server.py
```

然后打开 <http://127.0.0.1:8888>。也可以直接使用命令行：

```sh
python3 main.py path/to/chat-export.json --limit 5
```

建议先用脱敏的测试 JSON 验证流程，再接入截图 OCR 或第三方模型。命令行会输出分析结果，网页模式会生成可下载的 HTML、CSV 和 JSON 报告。

先制作脱敏副本，再交给分析器：

```sh
python3 tools/sanitize_export.py raw-export.json work/sanitized-export.json
python3 main.py work/sanitized-export.json --mode json --output-dir reports
```

脚本会处理常见手机号、邮箱、微信号、订单号以及客户/客服字段，但不能保证识别所有个人信息。分享或上传前仍需人工检查。

## 与 1688 插件配合使用

如果你使用 [1688-chat-downloader](https://github.com/794249481-droid/1688-chat-downloader) 或 [lai-assistant](https://github.com/794249481-droid/lai-assistant)，可以把前一天导出的询盘记录作为本项目的输入。建议每天按以下顺序复盘：

`导出前一天记录 → 删除客户身份信息 → 导入 JSON 或截图 → 检查客户分级和需求摘要 → 人工确认 → 输出跟进清单`

通过多日结果的汇总，可以进一步观察客户反复关注的产品功能、规格、包装、交期和价格，帮助销售团队发现产品改进机会和市场需求动向。分析器只提供辅助判断，不会替代人工确认，也不会自动证明某个市场趋势。

## Daily sales review workflow

Use an export from the 1688 extensions as a sanitized input. The analyzer can organize conversations into lead priority, customer needs, objections, product questions, follow-up suggestions, and machine-readable reports. Review the result manually before using it for customer communication, sales coaching, or product decisions.

截图 OCR 功能依赖项目外部的 OCR 工具；JSON 分析和报告生成可以先独立验证。图片分析还需要配置 DeepSeek API：

```sh
export DEEPSEEK_API_KEY="你的密钥"
```

不要把密钥、真实聊天记录、生成报告、`uploads/` 或 `output/` 提交到 Git。程序默认绑定 `127.0.0.1`，因为网页接口没有登录认证；只有在受控网络中并自行补充认证后，才应设置 `CHAT_ANALYZER_HOST` 对外监听。

## 输入与输出

- JSON：使用导出的聊天 JSON 文件。
- 图片：上传截图目录或 ZIP，生成 OCR 与分析结果。
- 报告：写入 `output/`，上传文件暂存于 `uploads/`；这两个目录只用于本地运行。

本项目是从个人业务原型中抽取出的通用代码，公开版本不含客户数据、账号信息、运行日志或密钥。分析结论是辅助参考，不应替代人工质检或客户隐私管理。

## 当前限制

系统没有内置用户登录、租户隔离或细粒度权限控制；不要直接部署到公网。第三方模型调用会把所选输入发送到对应 API，使用前应确认组织的数据处理政策。当前未指定开源许可证。
