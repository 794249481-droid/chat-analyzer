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
