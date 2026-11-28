# 贡献指南（Contributing to lingual-audit）

感谢你关注并愿意改进 `lingual-audit`。本项目采用 [MIT License](LICENSE) 开源，欢迎通过 Issue 或 Pull Request 一起完善工具链和审译规范。

## 欢迎贡献的方向

1. **多格式语料解析器增强**（`src/lingual_audit/corpus/`）
   - 改进 `PDF`、`HTML`、`XLSX`、`CSV` 术语表与规范文档的表格抽取、断行还原和字符归一化能力。
2. **行业无关的测试夹具与黄金集样例**（`tests/fixtures/`）
   - 补充跨行业（如医疗、制造、SaaS、电商、通用 Web）的完全脱敏测试样例，帮助验证检索召回、冲突裁决和跨菜单一致性。
3. **浏览器采集器增强**（`src/lingual_audit/explore/`）
   - 扩展实验性采集器对更多标准无障碍/语义属性（如 ARIA 角色、表单标签关联）的识别，同时严格保持只读默认行为与 `thorough` 模式断网隔离。
4. **文档与多语言支持**
   - 补充更多语言的 README、实战示例或故障排查说明。

## 本地开发环境

需要 Python 3.11 或更高版本：

```bash
git clone <repo-url>
cd lingual-audit

python3 -m venv --system-site-packages .venv
.venv/bin/pip install -e ".[dev,browser]"
```

如果你需要运行 Playwright 浏览器集成测试，请确认已安装 `playwright>=1.48` 及对应的 Chromium 浏览器二进制。

## 提交 PR 前的必跑检查

所有代码与文档改动在提交 PR 前，请确保以下确定性检查全部通过：

```bash
# 1. 代码风格与静态检查
.venv/bin/ruff check src tests

# 2. 单元测试与契约校验
.venv/bin/pytest -v -p no:anyio

# 3. 脱敏与敏感路径扫描（必须返回 clean: true）
.venv/bin/lingual-audit sanitize-check .
```

如果改动涉及 `pyproject.toml` 或 `contracts/*.schema.json`，请顺手验证 wheel 打包后在仓库外也能正常执行 `validate`：

```bash
.venv/bin/python3 -m pip wheel --no-deps --no-build-isolation -w /tmp/wheel-check .
```

## 安全与脱敏底线

提交任何代码、测试夹具或示例文档时，必须遵守以下底线：

- **禁止提交真实业务数据与内部信息**：测试样例中不得包含真实公司名、内部产品代号、客户/订单数据、内网 IP 或真实域名。
- **禁止提交凭据与会话状态**：不得提交 `.env*`、API Key、Cookie 或浏览器 `storage-state` 文件。
- **不破坏共享契约**：修改 `contracts/*.schema.json` 或 `src/lingual_audit/contracts.py` 时，必须同步更新相关测试和文档，且所有现有契约回归测试保持通过。
- **保持行业无关**：核心代码和默认模板不得硬编码特定行业的私有规则，行业配置统一通过外部工作区的 `corpus/` 和 `authority_policy.json` 注入。

## 提交流程

1. Fork 本仓库并从 `main` 拉取新分支（例如 `feat/csv-parser-improvement` 或 `fix/citation-check`）。
2. 为你的改动补充对应的单元测试或回归测试。
3. 运行上面的 `ruff`、`pytest` 和 `sanitize-check` 检查。
4. 提交清晰的 Commit 信息并开启 Pull Request，在 PR 描述里写明改动背景、影响范围与验证命令。
