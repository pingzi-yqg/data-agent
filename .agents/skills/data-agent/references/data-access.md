# 数据访问

## 环境

- `cn`：中国和菲律宾环境。
- `id`：印度尼西亚环境。

连接配置使用 `CN_MCP_URL`/`CN_MCP_TOKEN` 与 `ID_MCP_URL`/`ID_MCP_TOKEN`。令牌只保存在本地 `.env`。

## 元数据

```text
data_agent.py metadata sources --server cn
data_agent.py metadata databases --server cn
data_agent.py metadata tables --server cn --database <库>
data_agent.py metadata columns --server cn --database <库> --table <表>
```

只有知识库无法证明字段或需要探查实时结构时才调用元数据接口。

## 查询

```text
data_agent.py query run --server cn|id --sql-file <code.sql> --output <data/result.csv> --timeout 600
```

执行顺序为：安全校验 → 提交 → 轮询 → 获取最多 10000 行 → UTF-8 BOM CSV → 元数据 JSON。默认拒绝覆盖已有输出文件。

查询后检查行数、列数、空结果、截断、比率范围、负金额、时间范围和需求字段覆盖情况。
