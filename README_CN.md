# Claude Usage Insight

**[English](README.md)**

Claude Code 本地用量分析工具。从 `~/.claude` 数据生成 HTML 报告 — 不调 API，数据不出本机。

![报告预览](screenshots/report-7d.png)

## 功能

- 扫描 `~/.claude/projects/**/*.jsonl` 获取每次请求的 token 用量
- 按项目、session、小时、模型聚合
- 按模型分别计算预估 API 费用（Opus/Sonnet/Haiku 各自定价）
- 检测缓存重建（cold start）并标记高成本 session
- 生成暗色主题 HTML 报告：
  - 概览卡片（费用、token 总量、session 数、缓存命中率）
  - 活跃度图表（单天按小时、多天按天）
  - 活跃模式图（日期×小时，仅多天模式）
  - 项目分布（含占比条形图）
  - Session 列表（token 分类：input/output/cache/cold starts）
  - Insights 表（缓存重建详情 + 最大请求 token 拆解）

## 快速开始

**最简单的方式：直接在 Claude Code 对话框里问。** 比如"看用量"、"最近7天花了多少"、"上个月的报告" — 会自动生成 HTML 报告并打开。

### 作为 Claude Code Skill

```bash
cp -r claude-usage-insight ~/.claude/skills/
```

在 Claude Code 中直接用：

```
/claude-usage-insight
```

或者自然语言：`看用量`、`看最近7天`、`看上个月`

### 命令行

```bash
# 今天的报告（macOS 自动打开浏览器）
python3 scripts/claude_usage_insight.py report --preset today

# 最近 7 天
python3 scripts/claude_usage_insight.py report --last 7d

# 最近 30 天
python3 scripts/claude_usage_insight.py report --preset last-30d

# 自定义日期
python3 scripts/claude_usage_insight.py report --since 2026-03-01 --until 2026-03-31

# 纯文本摘要
python3 scripts/claude_usage_insight.py summary --preset today

# 按项目排名
python3 scripts/claude_usage_insight.py top --by project --last 7d
```

## 时间范围

| 预设 | 说明 |
|------|------|
| `today` | 今天（默认） |
| `yesterday` | 昨天 |
| `last-7d` | 最近 7 天（含今天） |
| `last-30d` | 最近 30 天（含今天） |
| `this-month` | 本月 |
| `last-month` | 上月 |

自定义：`--since YYYY-MM-DD --until YYYY-MM-DD` 或 `--last 24h|72h|7d`

## 报告内容

### 单天
- 24 小时柱状图，hover 看详情

### 多天
- 按天柱状图
- 活跃模式：GitHub 风格贡献图（日期×小时网格，6 级色阶）

### 固定模块
- **概览卡片**：预估费用、总 token、session 数、请求数、缓存命中率
- **项目分布**：token 量 + 占比 + 条形图
- **Session 列表**：Top 30，含日期时间、项目、prompt、input/output/cache 分类、cold start 次数
- **Insights**：逐 session 分析 — 缓存重建详情（次数、主线程/子任务拆分、费用、时间线）+ 最大请求 token 拆解（Cache Write / Cache Read / Input / Output）

## 定价模型

按每个请求实际使用的模型计算：

| 模型 | Input | Output | Cache Write | Cache Read |
|------|-------|--------|-------------|------------|
| Opus 4.6 | $5/M | $25/M | $6.25/M | $0.50/M |
| Sonnet 4.6 | $3/M | $15/M | $3.75/M | $0.30/M |
| Haiku 4.5 | $1/M | $5/M | $1.25/M | $0.10/M |

显示的是"如果按 API 付费要花多少" — Max 订阅用户实际不按这个计费。

## 文件命名

报告写入 `~/.claude/usage-data/reports/`，文件名幂等：

- `claude-usage-today.html`
- `claude-usage-last-7d.html`
- `claude-usage-2026-03-01_to_2026-03-31.html`

同范围重跑覆盖旧文件，不堆积。

## 数据来源

| 来源 | 内容 |
|------|------|
| `~/.claude/projects/**/*.jsonl` | 请求级 token 用量 |
| `~/.claude/usage-data/session-meta/*.json` | session 元数据、工具调用 |
| `~/.claude/usage-data/facets/*.json` | 语义标签（目标、结果） |
| `~/.claude/history.jsonl` | 首条 prompt 文本（兜底） |

## 环境要求

- Python 3.9+
- 无外部依赖（纯标准库）
- macOS 自动打开 HTML 报告

## 致谢

灵感来自 [ccusage](https://github.com/ryoppippi/ccusage)。定价数据参考 [LiteLLM](https://github.com/BerriAI/litellm)。

## License

MIT
