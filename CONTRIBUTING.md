# 参与贡献

欢迎给 `lingual-audit` 提 Issue 或 Pull Request。本项目使用 [MIT License](LICENSE) 开源。

## 可以帮上忙的方向

1. **语料解析器**（`src/lingual_audit/corpus/`）：改进 `PDF`、`HTML`、`XLSX`、`CSV` 里的表格提取、断行拼接和字符归一化。
2. **脱敏测试夹具与黄金集样例**（`tests/fixtures/`）：补充医疗、制造、SaaS、电商等跨行业的脱敏样例，用来验证召回、冲突裁决和跨菜单一致性。
3. **浏览器采集器**（`src/lingual_audit/explore/`）：帮实验性采集器识别更多标准语义属性（如 ARIA 角色、表单标签关联），同时保持只读默认行为和 `thorough` 模式下的断网隔离。
4. **文档与翻译**：补充其他语言的 README、实战例子或常见问题说明。

## 本地环境

需要 Python 3.11 或以上版本：

```bash
git clone <repo-url>
cd lingual-audit

python -m venv .venv
source .venv/bin/activate  # Windows PowerShell: .venv\Scripts\Activate.ps1
python -m pip install -e ".[dev,browser]"
```

如果要跑 `explore` 或 Playwright 浏览器集成测试，需要安装 `playwright>=1.48` 并下载 Chromium：

```bash
python -m playwright install chromium
```

## 提交 PR 前跑一遍检查

```bash
# 代码风格检查
ruff check src tests examples

# 单元测试与契约测试
pytest -v -p no:anyio

# 脱敏与敏感路径扫描（需返回 clean: true）
lingual-audit sanitize-check .
```

如果改了 `pyproject.toml` 或 `contracts/*.schema.json`，顺手打个 wheel 确认 Schema 都打进去了：

```bash
python -m pip wheel --no-deps -w dist .
```

## 提交前需要注意的几件事

- **不要提交真实业务数据**：测试样例里不能出现真实公司名、内部项目代号、客户或订单数据、内网 IP 和真实域名。
- **不要提交凭据和登录态**：不要把 `.env*`、API Key、Cookie 或浏览器 `storage-state` 放进仓库。
- **保持契约同步**：改 `contracts/*.schema.json` 或 `src/lingual_audit/contracts.py` 时，把对应的测试和文档一起更新掉。
- **保持行业无关**：核心代码和默认模板不写死特定行业规则，行业术语和权威排序统一通过外部工作区的 `corpus/` 和 `authority_policy.json` 配置。

## 提交流程

1. Fork 仓库并从 `main` 切一个新分支（例如 `feat/csv-parser` 或 `fix/citation-check`）。
2. 补上对应的单元测试或回归测试。
3. 跑完 `ruff`、`pytest` 和 `sanitize-check`。
4. 提交清晰的 Commit 信息并开 PR，写明改动背景和验证方式。
