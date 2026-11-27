# lingual-audit

[English Documentation (README.md)](README.md)

面向**复杂 Web 系统**的**行业无关、零外部 Skill 依赖、自包含 Agent Skill + 确定性 Python CLI / RAG 工具链**，用于完成「中英双语界面全量盘点 → 自带术语库（BYOG）混合检索审译 → 6 列着色防公式注入 Excel 交付与回读验收」端到端闭环。

> **为什么做这个项目？**
> 复杂 Web 系统（涵盖金融、医疗、制造、SaaS、电商、开发者平台等任意领域）往往在数百个页面中沉淀了数千条错译、漏译、裸露 i18n Key 或跨菜单译名漂移的界面词条。纯人工盘点与校对周期动辄数月；而直接让大模型临时翻阅参考 PDF 又面临漏召回、出处幻觉、同词异译漂移以及误触发前端自动保存等工程风险。`lingual-audit` 将**概率性 LLM 语义审译**与**确定性采集、检索、出处核验和 Excel 回读**彻底解耦。

---

## 核心特性

- **彻底行业无关（BYOG 自带术语库）**：默认初始化空白知识库槽位（`corpus/raw/`、`corpus/manifest.json`、`corpus/authority_policy.json`）。无论什么行业，只需把本领域的 `PDF`、`HTML`、`XLSX`、`CSV` 术语表或规范丢进去即可运行，零代码改动；空库状态下也能优雅降级（`no_authoritative_hit=True`），不报错、不编造出处。
- **零 Vendor 自包含 Agent Skill**：原生 [SKILL.md](SKILL.md) + [references/](references) + [templates/](templates) 吸收了通用页面探索与文件化断点续跑思路，无需安装或 vendor 第三方 Skill，跨宿主（Claude Code / Codex / Antigravity / WorkBuddy）开箱即用。
- **分层探索深度 + `thorough` 网络阻断安全护栏**：支持 `quick` / `standard` / `thorough` 三档。在 `thorough` 模式下允许填入合成测试数据以触发前端校验报错，但强制在独立隔离 Playwright Context 中禁用 Service Worker 并**全程阻断 HTTP / WebSocket 出站请求直至销毁该 Context**（连延迟防抖自动保存也彻底拦死）；无法建立网络隔离的宿主自动跳过输入探测。
- **单文件 SQLite FTS5 + 适用性优先仲裁**：用 `pdfplumber` / `BeautifulSoup` / `openpyxl` / `csv` 解析多格式语料，保留原文与 NFKC 归一化双视图（自动修复常见 PDF 中的康熙部首偏旁如 `⼿`/`⽅`），建立精确短缩写通道 + CJK 二元组索引，先按「领域 / 地区 / 目标语言变体 / 双语性质」筛选适用性，再按可配置策略排序，严禁英文单语定义冒充中文译名。
- **6 列着色防公式注入 Excel 交付与自动回读**：按功能菜单分 Sheet 输出 `页面层级 | 系统英文 | 系统中文 | AI 建议中文 | 比对结论 | 修改理由与业务依据`，7 类结论状态各有柔和底色区分，非`保留`项强制填写业务向理由与出处，所有文本单元严格防公式注入（`=SpotRate + Margin` 等原样保留为纯文本），并自动执行 `openpyxl` 回读校验与 SHA-256 哈希核对。

---

## 产品设计思考与关键取舍（Design Decisions & Trade-offs）

1. **为什么先做“适用性筛选”，而不是给所有机构排一个全局优先级？**
   在多地区复杂 Web 系统中，港台译名（`zh-HK`）与内地规范（`zh-CN`）往往属于合法地区变体而非错误；英文单语国际标准只能解释概念含义，不能用来强行背书中文译名。先按领域、地区、语言变体和双语性质过滤，再在同适用范围内按 `authority_policy.json` 裁决，既严谨又能把真实冲突透明交给业务方拍板。
2. **为什么首期用“精确词条 + SQLite FTS5 + CJK 二元组”，再通过消融评测决定是否引入向量？**
   Web 界面词条高度偏短（如 2–3 字母缩写或 2 字中文术语），纯向量召回易漂移，且 SQLite 内置 `trigram` 分词器无法匹配不足 3 字符的查询。单文件 SQLite + FTS5 + NFKC/二元组方案零运维、零 API 成本、100% 离线可复现，同时预留了 RRF 向量融合接口，方便在黄金集上做三组消融实验（裸读 vs 词条+FTS vs 混合向量）用真实数据说话。
3. **为什么 `thorough` 填入测试数据必须上独立隔离 Context 网络阻断？**
   单靠“不点提交按钮”不足以证明安全——大量现代 Web 表单会在 `input`、`change`、`blur` 甚至延迟防抖定时器中发 `POST` 自动保存。在独立隔离 Context 内禁用 Service Worker、全程阻断 HTTP/WebSocket 并在探测结束后直接销毁该 Context，才能在物理层保证零后端写入。
4. **为什么选择 6 列着色 Excel + 同包 JSON 清单？**
   业务方复核需要一眼看清页面层级、用颜色快速筛选待改项、并读懂从业务出发的修改理由；工程验收则需要行级 SHA-256 与可机器核验的出处 ID。两者分离，既保持表格清爽可读，又不丢失任何可追溯证据。

---

## 快速上手

```bash
# 1. 创建虚拟环境并安装
python3 -m venv --system-site-packages .venv
.venv/bin/pip install -e ".[dev,browser]"

# 2. 探测本机运行能力
.venv/bin/lingual-audit doctor --json

# 3. 初始化外部工作区（自动生成空白 BYOG 术语库槽位与默认策略）
.venv/bin/lingual-audit run init \
  --workspace /tmp/my-audit-ws \
  --run-id RUN-20261127-0001 \
  --app-label "示例复杂 Web 系统" \
  --industry "general_web" \
  --depth standard

# 4. 将本领域的 PDF/HTML/XLSX/CSV 放入 /tmp/my-audit-ws/corpus/raw/
#    并在 manifest.json 登记后，执行摄取与建库：
.venv/bin/lingual-audit corpus ingest --manifest /tmp/my-audit-ws/corpus/manifest.json --output-dir /tmp/my-audit-ws/corpus/normalized
.venv/bin/lingual-audit corpus index --normalized-dir /tmp/my-audit-ws/corpus/normalized --db-path /tmp/my-audit-ws/corpus/index.sqlite

# 5. 运行确定性测试集
.venv/bin/pytest -v -p no:anyio
```

---

## 致谢

本项目的页面探索与文件化续跑设计受 `app-docs` 与 `planning-with-files` 启发，但未将任何第三方 Skill 打包进本仓库；所有规范、模板与工具链均基于 MIT 协议独立实现。
