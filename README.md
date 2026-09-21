# Data Agent

Data Agent 是一个面向 Codex 的知识驱动数据分析工作流。它将指标口径检索、数据血缘追溯、只读 SQL 执行、CSV 质量检查和可审计报告组合成一条端到端链路。

## 为什么需要它

企业取数的难点通常不是 SQL 语法，而是指标口径、字段含义、数据源和查询边界。Data Agent 要求每个关键决策都有知识库、表结构或实际探查证据，不猜测表名、字段和枚举值。

## 核心能力

- 六种工作模式：完整分析、取数、探索、报表、SQL 订正和知识检索
- Concept、BI 报表、Hive 表和历史经验的 FTS5 分组检索
- “指标 → 报表 → 数据表”的结构化来源链路
- MCP 数据源、数据库、表和字段元数据浏览
- Hive SQL 只读安全检查与有界查询
- CSV 空结果、截断、异常比率和负金额检查
- 分阶段审计与可追溯 HTML 报告

## 工作流

```text
自然语言需求
→ 意图和口径识别
→ 指标及数据血缘检索
→ SQL 生成与只读校验
→ 数据查询与质量检查
→ 可追溯报告与项目审计
```

## 快速开始

1. 复制 `.env.example` 为 `.env`，填写自己的只读 MCP 地址和令牌。
2. 准备包含 `knowledge/` 目录的知识包。
3. 在仓库根目录启动 Codex，使用 `$data-agent` 或直接描述数据需求。

```text
python .agents/skills/data-agent/scripts/data_agent.py knowledge import --zip knowledge.zip
python .agents/skills/data-agent/scripts/data_agent.py knowledge status
python .agents/skills/data-agent/scripts/data_agent.py knowledge search "country business-line metric" --grouped
python .agents/skills/data-agent/scripts/data_agent.py knowledge trace "metric"
```

## 常用命令

```text
python .agents/skills/data-agent/scripts/data_agent.py project init --project projects/demo --mode knowledge --request "define conversion rate"
python .agents/skills/data-agent/scripts/data_agent.py sql validate --file projects/demo/code.sql
python .agents/skills/data-agent/scripts/data_agent.py query run --server cn --sql-file projects/demo/code.sql --output projects/demo/data/result.csv
python .agents/skills/data-agent/scripts/data_agent.py data check --file projects/demo/data/result.csv
python .agents/skills/data-agent/scripts/data_agent.py report build --project projects/demo
python .agents/skills/data-agent/scripts/data_agent.py audit check --project projects/demo --scope sql
python .agents/skills/data-agent/scripts/data_agent.py audit project --project projects/demo
```

## 安全边界

- 数据库查询只允许单条 `SELECT` 或 `WITH ... SELECT`。
- 令牌只保存在本地 `.env`，不应提交到仓库。
- `knowledge/`、`projects/` 和本地索引默认被 Git 忽略。
- 默认拒绝专属财务、人事和薪酬数据源。
- 写库、删除知识文档和对外发布需要单独授权。

## 测试

```text
python -m unittest discover -s tests -v
```

## 仓库内容

```text
.agents/skills/data-agent/   Codex 技能、CLI 和工作流规则
examples/demo/               不含真实业务数据的离线演示
tests/                       安全校验、报告和知识导入测试
```

