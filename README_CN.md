# lingual-audit

[English Documentation (README.md)](README.md)

`lingual-audit` 是一个面向复杂 Web 系统的双语界面审译工具，包含一套自包含的 Agent Skill 和对应的 Python 命令行工具。它可以把运行中系统的中英文界面词条盘点出来，结合本地自带术语库、联网查证缓存和大模型语言判断完成审译与证据包级引用核验，最后按模块增量导出带颜色标注、防公式注入的 6 列 Excel 审查表。

金融、医疗、制造、SaaS、电商、开发者平台这类复杂 Web 系统，跑久了通常会在几百个页面里攒下几千条错译、漏译、裸露 i18n Key 或跨菜单译名不统一的问题。靠人一页页翻查往往要花好几周；直接把几本参考 PDF 丢给大模型看，又容易漏掉短词、编造出处、前后菜单译法打架，甚至在点页面时误触表单自动保存。`lingual-audit` 把这两件事拆开了：语言判断交给大模型，本地检索、引用核验和 Excel 回读全部交给确定性脚本来做。

## 主要功能

- **自带术语库（BYOG）+ 联网证据落盘**：初始化工作区时会生成空白术语库目录（`corpus/raw/`、`corpus/manifest.json`、`corpus/authority_policy.json`）。无论什么行业，把 `PDF`、`HTML`、`XLSX` 或 `CSV` 格式的术语表和规范放进去就能直接跑，不需要改代码。本地库没命中时，可以让宿主 Agent 联网查证，再用 `lingual-audit corpus add-web` 把网页 URL 和原文摘录落盘进 `corpus/web_cache/`，直接进入同一套本地索引和哈希核验流程；同时支持在 `authority_policy.json` 里按域名配置权威等级。
- **开箱即用的 Agent Skill**：仓库内置 [SKILL.md](SKILL.md)、[references/](references) 和 [templates/](templates)，在 Claude Code、Codex、Antigravity、WorkBuddy 中可以直接用，没有把第三方 Skill 打包进仓库。
- **按模块增量落盘交付**：每完成一个功能模块的采集和审译，直接把该模块落盘到 `pages/<page_id>.json` 并合并进 `review.json`，随即导出或更新 Excel 工作簿，后续每做完一个模块就往里补一个 Sheet，不用等几百个页面全扫完才看到成果。
- **单文件 SQLite FTS5 检索与证据包级引用核验**：内置 `pdfplumber`、`BeautifulSoup`、`openpyxl` 和 `csv` 解析器，同时保存原文和 NFKC 归一化文本（顺带修掉 PDF 里常见的康熙部首偏旁，比如 `⼿`、`⽅`）。索引层把短缩写精确匹配和 CJK 二元组检索分开建通道，检索时先按领域、地区、目标语言变体和双语性质过滤适用范围，再按配置排序。`review validate` 会自动对照 `packets.json` 校验每条引用必须来自当前词条的证据包，拦住无证据改译和跨词条错配引用。
- **实验性自动采集器与网络隔离**：`lingual-audit explore` 目前为实验性功能（需要 `playwright>=1.48`），支持 `quick`、`standard`、`thorough` 三档。该采集器依赖 `[data-menu]`、`[data-i18n]`、`placeholder` 等标准语义标记，如果页面抓到 0 条词条会如实标为 `blocked` 而不会误报 `fully_inspected`。面对自定义 DOM 的真实系统，推荐用宿主浏览器工具或已录入的 UI JSON 样例走主流程。
- **6 列着色 Excel 交付与自动回读**：按功能菜单分 Sheet 输出 `页面层级 | 系统英文 | 系统中文 | AI 建议中文 | 比对结论 | 修改理由与业务依据`。6 种结论状态（`保留`、`建议修改`、`缺译`、`来源冲突`、`单语限制`、`待核对`）各自配了浅底色方便筛选；其中 `待核对` 只放确实没核对到双语的词条，已经核对到双语的词条直接明确给 `保留` 或 `建议修改`。所有单元格强制写为纯文本，像 `=SpotRate + Margin` 这类内容不会变成 Excel 公式，导出后还会自动用 `openpyxl` 回读核对内容和 SHA-256 哈希。

## 几个关键取舍

**先筛适用范围，再比来源优先级。**
多地区系统里，港台译名（`zh-HK`）和内地标准（`zh-CN`）很多时候只是地区习惯不同，不算错译；英文单语的国际标准能解释概念，但不能拿来给中文界面译名背书。先按领域、地区、语言变体和双语性质筛一遍，再在同一适用范围内按 `authority_policy.json` 裁决，遇到真实冲突直接标成`来源冲突`交给业务方拍板。

**先用精确词表 + SQLite FTS5 + CJK 二元组，向量检索做成可选消融项。**
Web 界面里的词条大多很短，比如两三个字母的缩写或者两个字的中文术语。纯向量检索在短词上容易跑偏，SQLite 自带的 `trigram` 分词器也匹配不了少于 3 个字符的词。单文件 SQLite 加上精确通道和二元组索引，不依赖外部服务和 API Key，本地离线就能稳定复现结果。仓库里同时预留了 RRF 向量融合接口，需要的话可以在黄金集上跑三组消融对比（裸读 vs 词表+FTS vs 混合向量），看数据再决定是否开启。

**`thorough` 模式输入测试数据时，必须上独立 Context 断网。**
只靠“不点提交按钮”并不安全，现在很多 Web 表单在 `input`、`change`、`blur` 甚至防抖定时器里就会发 `POST` 自动保存。在独立隔离的 Context 里禁掉 Service Worker、全程拦住 HTTP/WebSocket 请求（依赖 `playwright>=1.48`）、测完立刻销毁 Context，才能确保不会往后端写脏数据。

**把人工看审的 Excel 和机器校验的 JSON 分开。**
业务同学看表时，希望左边一眼看清页面层级、中间靠颜色筛出待改项、右边直接看到业务角度的修改理由；工程验收则需要行级 SHA-256 哈希和可核验的出处 ID。Excel 保持 6 列干净好读，完整的结构化出处和校验信息放在同目录的 `review.json` 和 `delivery-manifest.json` 里，两边互不干扰。

## 快速上手

```bash
# 1. 创建虚拟环境并安装（支持源码 editable 安装或 wheel 安装）
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
#    并在 manifest.json 登记（也可随时用 corpus add-web 录入联网查证片段），再执行摄取与建库：
.venv/bin/lingual-audit corpus ingest --manifest /tmp/my-audit-ws/corpus/manifest.json --output-dir /tmp/my-audit-ws/corpus/normalized
.venv/bin/lingual-audit corpus index --normalized-dir /tmp/my-audit-ws/corpus/normalized --db-path /tmp/my-audit-ws/corpus/index.sqlite

# 5. 运行确定性测试集
.venv/bin/pytest -v -p no:anyio
```

## 致谢

页面探索和文件化续跑的思路参考了 `app-docs` 与 `planning-with-files`，本仓库未打包任何第三方 Skill，所有规范、模板和工具链均按 MIT 协议独立实现。
