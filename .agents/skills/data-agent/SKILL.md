---
name: data-agent
description: 使用企业知识库完成指标口径检索、自然语言数据需求解析、Hive SQL 生成与只读执行、CSV 核验、探索分析和可追溯报告。适用于知识检索、取数、完整分析、报表制作、SQL 订正与探索；不用于缺少数据依据的通用写作。
---

# Data Agent

先识别模式：`analysis` 完整分析、`extract` 取数、`explore` 探索、`report` 报表、`sql-fix` SQL订正、`knowledge` 知识检索。需求清楚时直接推进；需要创建项目时使用 `projects/<简短名称>_<日期>/`，不得覆盖既有文件。

## 共同原则

- 事实、字段、枚举、JOIN 键和指标口径必须有知识库、表结构或本次探查证据；证据不足时明确标注，不补造。
- 先查 Concept；命中后继续查其 `source_reports` 和 `source_tables` 对应的 Tableau、Hive 文档。
- 生成 SQL 前搜索 Memory 中的相关经验教训。
- 查询只接受单条 `SELECT` 或 `WITH ... SELECT`。写库、删知识、发布报告或持久化外部记忆需要用户另行授权。
- 报告数字必须能追溯到 `data/*.csv` 或明确公式；比率用分子/分母重算。

## 快速入口

```text
python .agents/skills/data-agent/scripts/data_agent.py knowledge status
python .agents/skills/data-agent/scripts/data_agent.py knowledge import --zip <knowledge.zip>
python .agents/skills/data-agent/scripts/data_agent.py knowledge search "<国家 业务线 指标>" --grouped
python .agents/skills/data-agent/scripts/data_agent.py knowledge trace "<国家 业务线 指标>"
python .agents/skills/data-agent/scripts/data_agent.py project init --project <目录> --mode <模式> --request "<需求>"
python .agents/skills/data-agent/scripts/data_agent.py project stage --project <目录>
```

首次使用、知识内容更新或导入后运行 `knowledge build`。导入到已有 `knowledge/` 时，只有用户明确要求替换才使用 `--replace`。

## 按模式执行

- `knowledge`：输出检索结构（国家、业务线、查询词、歧义），执行分组检索和 `knowledge trace`，读取概念及来源报表/表；只交付有证据的定义、公式、筛选器和来源。必要时用 `knowledge aliases` 解释业务黑话。
- `extract`：记录需求，检索知识和 lessons，生成 SQL，按 [SQL 规则](references/sql-rules.md) 校验后执行，并运行 `data check`。
- `analysis`：按取数流程得到 CSV，再根据 [报告规则](references/report-rules.md) 生成报告，最后执行全项目审计。
- `explore`：从小范围查询开始，每轮解释发现与下一步；保持同一项目，不将探索性结论写成事实。
- `report`：仅使用现有 CSV，先检查数据，再生成 `report/report.json` 和 HTML。
- `sql-fix`：保留原始意图，逐项核对表、字段、口径、分区、粒度和 LIMIT；不执行，除非用户同时要求取数。

详细模式路由与文件结构见 [工作流](references/workflows.md)。MCP 元数据和查询命令见 [数据访问](references/data-access.md)。审计判定见 [质量检查](references/quality-rules.md)。

## 强制检查点

1. SQL 生成后：`sql validate --file <项目>/code.sql`。
2. 查询后：`data check --file <项目>/data/<文件>.csv`，检查空数据、截断、列宽、负金额和异常比率。
3. 阶段交付前：`audit check --project <项目> --scope request|sql|data|report`。
4. 完整交付前：`audit project --project <项目>`。

确定性错误必须修复后再继续；歧义或经验范围偏离作为警告呈现给用户。

## 安全边界

- 不读取或输出 `.env` 中的令牌。
- 不访问名称包含 `finance`、`financial`、`hr`、`salary` 或 `payroll` 的专属数据源。
- `df` 快照表使用单个 `dt = 'yyyyMMdd'` 分区；`di` 表限制 `dt` 范围；明细查询必须有界。
- 不复制 Claude 专属配置、Hook、私有权限文件或平台凭据。
