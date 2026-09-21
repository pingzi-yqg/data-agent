# 工作流与项目结构

## 项目结构

```text
projects/<名称>_<日期>/
├── request.md
├── code.sql
├── code_explanation.md
├── data/
│   ├── result.csv
│   └── result.meta.json
└── report/
    ├── report.json
    └── report.html
```

`project stage` 根据现有文件识别下一阶段：request → sql → data → report → complete。

## 需求记录

`request.md` 至少记录：原始目标、国家/环境、时间范围与粒度、指标、维度、筛选条件、受众和交付物。未提供的信息写 `-`，不要猜测。

## 知识检索

检索结构：

```text
Facet: country=<代码>, biz_line=<业务线>
Query: <指标或表名>
Route: <意图> → concept → tableau/hive → memory
Lessons: 需要/不需要
歧义点: <待确认内容>
```

用 `knowledge search ... --grouped` 分别返回 Concept、Hive、Tableau、Memory。Concept 命中后必须读取文档，再用其中的报表名和表名分别检索对应范围。生成 SQL 前独立搜索 `经验教训 <关键词>`。

## 模式路由

- 知识检索：不生成 SQL；交付定义、公式、来源报表、来源表、关键筛选和可信度。
- 取数：完成到 CSV 与数据检查；报告不是必需。
- 完整分析：需求、SQL、数据、报告、全项目审计均需完成。
- 探索：允许多轮小查询；每轮保留 SQL 与 CSV，避免覆盖。
- 报表：不得自行补造缺失数据或结论。
- SQL订正：交付修订 SQL、变更说明和证据；默认不执行。
