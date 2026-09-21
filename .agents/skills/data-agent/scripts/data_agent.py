#!/usr/bin/env python3
"""Data Agent CLI: knowledge search, safe MCP queries, reports, and audits."""

from __future__ import annotations

import argparse
import csv
import html
import json
import os
import re
import sqlite3
import sys
import time
import urllib.request
import zipfile
import shutil
from collections import defaultdict
from datetime import datetime
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parents[4]
INDEX = ROOT / "index.db"
KNOWLEDGE = ROOT / "knowledge"
SOURCES = {
    "concept": "concept-wiki/index.json",
    "hive": "hive-db/index.json",
    "tableau": "tableau-workbooks/index.json",
    "memory": "long-term-memory/index.json",
}
SOURCE_DIRS = {
    "concept": "concept-wiki",
    "hive": "hive-db",
    "tableau": "tableau-workbooks",
    "memory": "long-term-memory",
}
DOMAIN_TERMS = (
    "国内", "中国", "印尼", "印度尼西亚", "菲律宾", "催收", "风控", "投放", "客服", "客诉",
    "入催率", "逾期率", "催回率", "放款率", "通过率", "转化率", "坏账率", "余额", "本金",
)
FORBIDDEN_SQL = {
    "insert", "update", "delete", "merge", "drop", "alter", "truncate",
    "create", "replace", "grant", "revoke", "call", "execute", "use",
    "set", "add", "dfs", "load", "export", "import",
}
SENSITIVE = re.compile(r"(?:^|[._])(finance|financial|hr|human_resources|salary|payroll)(?:[._]|$)", re.I)


def load_env() -> None:
    path = ROOT / ".env"
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.split("#", 1)[0].strip())


def flatten(value: object) -> str:
    if isinstance(value, dict):
        return " ".join(f"{k} {flatten(v)}" for k, v in value.items())
    if isinstance(value, list):
        return " ".join(flatten(v) for v in value)
    return "" if value is None else str(value)


def _load_items(path: Path) -> list[dict]:
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    if isinstance(data, dict):
        data = data.get("items", data.get("data", data.get("entries", [])))
    return [item for item in data if isinstance(item, dict)] if isinstance(data, list) else []


def lookup_aliases(query: str) -> list[dict]:
    path = KNOWLEDGE / "alias-mapping" / "company_wiki_slang.json"
    if not path.exists():
        return []
    hits = []
    seen = set()
    for item in _load_items(path):
        name = str(item.get("standard_name", ""))
        if name in seen:
            continue
        if any(str(keyword) and str(keyword).lower() in query.lower() for keyword in item.get("keywords", [])):
            hits.append(item)
            seen.add(name)
    return hits


def expand_query(query: str) -> str:
    names = [str(item.get("standard_name", "")) for item in lookup_aliases(query)]
    return " ".join([query, *names]).strip()


def build_index(knowledge: Path) -> int:
    temp = INDEX.with_suffix(".tmp")
    if temp.exists():
        temp.unlink()
    conn = sqlite3.connect(temp)
    conn.execute("CREATE VIRTUAL TABLE docs USING fts5(scope, title, path, content, tokenize='unicode61')")
    count = 0
    for scope, relative in SOURCES.items():
        index_path = knowledge / relative
        if not index_path.exists():
            print(f"跳过：{index_path.relative_to(ROOT) if index_path.is_relative_to(ROOT) else index_path}")
            continue
        data = json.loads(index_path.read_text(encoding="utf-8-sig"))
        if isinstance(data, dict):
            data = data.get("items", data.get("data", []))
        if not isinstance(data, list):
            raise ValueError(f"索引必须是数组: {index_path}")
        for item in data:
            if not isinstance(item, dict):
                continue
            path = next((str(item[k]) for k in ("path", "doc_path", "file", "file_path") if item.get(k)), "")
            title = next((str(item[k]) for k in ("title", "name", "table_name", "表名", "doc_name") if item.get(k)), path)
            content = flatten(item)
            conn.execute("INSERT INTO docs(scope,title,path,content) VALUES(?,?,?,?)", (scope, title, path, content))
            count += 1
    conn.commit()
    conn.close()
    os.replace(temp, INDEX)
    print(f"索引已构建：{count} 条 → {INDEX}")
    return count


def knowledge_status(knowledge: Path = KNOWLEDGE) -> dict:
    sources = {}
    total = 0
    for scope, relative in SOURCES.items():
        path = knowledge / relative
        count = 0
        error = None
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8-sig"))
                if isinstance(data, dict):
                    data = data.get("items", data.get("data", []))
                count = len(data) if isinstance(data, list) else 0
            except (OSError, ValueError) as exc:
                error = str(exc)
        sources[scope] = {"path": str(path), "exists": path.exists(), "records": count, "error": error}
        total += count
    return {"knowledge_dir": str(knowledge), "index_exists": INDEX.exists(), "records": total, "sources": sources}


def _find_knowledge_root(extracted: Path) -> Path:
    candidates = [extracted / "knowledge", *extracted.glob("*/knowledge"), extracted]
    for candidate in candidates:
        if any((candidate / relative).exists() for relative in SOURCES.values()):
            return candidate
    raise FileNotFoundError("压缩包中未找到 knowledge/ 结构")


def import_knowledge(archive: Path, destination: Path = KNOWLEDGE, replace: bool = False) -> int:
    if not zipfile.is_zipfile(archive):
        raise ValueError(f"不是有效 ZIP：{archive}")
    temp = ROOT / ".knowledge-import-tmp"
    if temp.exists():
        shutil.rmtree(temp)
    temp.mkdir()
    try:
        with zipfile.ZipFile(archive) as handle:
            for info in handle.infolist():
                target = (temp / info.filename).resolve()
                if temp.resolve() not in target.parents and target != temp.resolve():
                    raise ValueError(f"ZIP 包含不安全路径：{info.filename}")
            handle.extractall(temp)
        source = _find_knowledge_root(temp)
        if destination.exists() and any(destination.iterdir()) and not replace:
            raise FileExistsError("knowledge/ 已存在；确认替换时使用 --replace")
        if destination.exists():
            shutil.rmtree(destination)
        shutil.copytree(source, destination)
    finally:
        if temp.exists():
            shutil.rmtree(temp)
    return build_index(destination)


def query_terms(query: str) -> list[str]:
    identifiers = re.findall(r"[A-Za-z][A-Za-z0-9]*(?:_[A-Za-z0-9]+)+", query)
    words = re.findall(r"[\u4e00-\u9fff]{2,}|[A-Za-z0-9]+", query)
    contained = [term for term in DOMAIN_TERMS if term in query]
    if len(contained) >= 2:
        words = [word for word in words if not (re.fullmatch(r"[\u4e00-\u9fff]{4,}", word) and word == query.strip())]
        words.extend(contained)
    result: list[str] = []
    for word in identifiers + words:
        if word not in result:
            result.append(word)
    return result


def search_index(query: str, scope: str, limit: int) -> list[dict]:
    if not INDEX.exists():
        raise FileNotFoundError("index.db 不存在，请先运行 knowledge build")
    terms = query_terms(expand_query(query))
    if not terms:
        return []
    variants = [" AND ".join(f'"{t}"' for t in terms), " OR ".join(f'"{t}"' for t in terms)]
    conn = sqlite3.connect(INDEX)
    conn.row_factory = sqlite3.Row
    rows: list[sqlite3.Row] = []
    for expression in variants:
        if scope == "all":
            rows = conn.execute(
                "SELECT scope,title,path,snippet(docs,3,'[',']','…',24) snippet,bm25(docs) score FROM docs WHERE docs MATCH ? ORDER BY score LIMIT ?",
                (expression, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT scope,title,path,snippet(docs,3,'[',']','…',24) snippet,bm25(docs) score FROM docs WHERE docs MATCH ? AND scope=? ORDER BY score LIMIT ?",
                (expression, scope, limit),
            ).fetchall()
        if rows:
            break
    conn.close()
    return [dict(row) for row in rows]


def search_grouped(query: str, scope: str = "all", top_k: int = 3) -> dict[str, list[dict]]:
    scopes = list(SOURCES) if scope == "all" else [scope]
    return {item: search_index(query, item, top_k) for item in scopes}


def resolve_document(scope: str, relative: str) -> Path:
    base = (KNOWLEDGE / SOURCE_DIRS[scope]).resolve()
    path = (base / relative).resolve()
    if base not in path.parents:
        raise ValueError("知识文档路径越界")
    if not path.exists():
        raise FileNotFoundError(path)
    return path


def trace_knowledge(query: str) -> dict:
    concept_path = KNOWLEDGE / SOURCES["concept"]
    if not concept_path.exists():
        raise FileNotFoundError("缺少 Concept 索引")
    terms = [term.lower() for term in query_terms(expand_query(query))]
    candidates = []
    for item in _load_items(concept_path):
        text = flatten(item).lower()
        score = sum(1 for term in terms if term in text)
        if score:
            candidates.append((score, item))
    if not candidates:
        return {"query": query, "concept": None, "reports": [], "tables": [], "confidence": "low"}
    candidates.sort(key=lambda pair: pair[0], reverse=True)
    concept = candidates[0][1]
    report_names = [name.strip() for name in str(concept.get("sources", concept.get("source_reports", ""))).split("|") if name.strip()]
    table_names = [name.strip() for name in str(concept.get("source_tables", "")).split("|") if name.strip()]
    reports = []
    country = str(concept.get("country", "")).upper()
    country_markers = {
        "CN": ("中国_China",),
        "ID": ("印尼_Indonesia",),
        "PH": ("菲律宾_Philippines",),
        "PL": ("波兰_Poland",),
        "ES": ("西班牙_Spain",),
    }
    report_index = KNOWLEDGE / SOURCES["tableau"]
    if report_index.exists():
        for item in _load_items(report_index):
            name = str(item.get("name", item.get("title", "")))
            location = str(item.get("project", item.get("file_path", "")))
            country_ok = not country_markers.get(country) or any(marker in location for marker in country_markers[country])
            if name in report_names and country_ok:
                reports.append(item)
    tables = []
    table_index = KNOWLEDGE / SOURCES["hive"]
    if table_index.exists():
        for item in _load_items(table_index):
            name = str(item.get("table_name", item.get("name", "")))
            qualified = f"{item.get('db_name')}.{name}" if item.get("db_name") else name
            if name in table_names or qualified in table_names:
                tables.append(item)
    return {
        "query": query,
        "concept": concept,
        "reports": reports,
        "tables": tables,
        "conflict": bool(concept.get("has_conflict")),
        "confidence": "high" if reports and tables and not concept.get("has_conflict") else "medium",
    }


def strip_sql(sql: str) -> str:
    sql = re.sub(r"--[^\n]*", " ", sql)
    sql = re.sub(r"/\*.*?\*/", " ", sql, flags=re.S)
    sql = re.sub(r"'(?:''|[^'])*'", "''", sql)
    sql = re.sub(r'"(?:""|[^"])*"', '""', sql)
    return sql


def validate_sql(sql: str) -> tuple[list[str], list[str], list[str]]:
    clean = strip_sql(sql).strip()
    errors: list[str] = []
    warnings: list[str] = []
    statements = [part.strip() for part in clean.split(";") if part.strip()]
    if len(statements) != 1:
        errors.append("只允许一条 SQL 语句")
    statement = statements[0] if statements else ""
    first = re.match(r"([A-Za-z]+)", statement)
    if not first or first.group(1).lower() not in {"select", "with"}:
        errors.append("只允许 SELECT 或 WITH ... SELECT")
    tokens = {token.lower() for token in re.findall(r"\b[A-Za-z_]+\b", statement)}
    blocked = sorted(tokens & FORBIDDEN_SQL)
    if blocked:
        errors.append("包含禁止关键字：" + ", ".join(blocked))
    tables = re.findall(r"\b(?:from|join)\s+([`\w.]+)", statement, flags=re.I)
    for table in tables:
        normalized = table.replace("`", "")
        if SENSITIVE.search(normalized):
            errors.append(f"禁止访问敏感数据源：{normalized}")
        short = normalized.rsplit(".", 1)[-1]
        if re.search(r"(?:^|_)df(?:_|$)", short, re.I):
            alias_match = re.search(rf"\b{re.escape(table)}\s+(?:as\s+)?(\w+)", statement, re.I)
            alias = alias_match.group(1) if alias_match else None
            target = rf"(?:{re.escape(alias)}\.)?dt" if alias else r"(?:\w+\.)?dt"
            if not re.search(target + r"\s*=\s*''", statement, re.I):
                errors.append(f"快照表 {normalized} 缺少 dt = 'yyyyMMdd' 等值分区")
    if not re.search(r"\blimit\s+\d+\b", statement, re.I):
        warnings.append("SQL 未包含 LIMIT；正式执行前建议限制结果规模")
    if "select *" in re.sub(r"\s+", " ", statement).lower():
        warnings.append("不建议 SELECT *，请明确输出字段")
    return errors, warnings, [table.replace("`", "") for table in tables]


class MCPClient:
    def __init__(self, server: str):
        load_env()
        prefix = server.upper()
        self.url = os.getenv(f"{prefix}_MCP_URL")
        self.token = os.getenv(f"{prefix}_MCP_TOKEN")
        if not self.url or not self.token:
            raise RuntimeError(f"缺少 {prefix}_MCP_URL 或 {prefix}_MCP_TOKEN")
        self.session_id: str | None = None
        self.request_id = 0
        self.send("initialize", {"protocolVersion": "2024-11-05", "capabilities": {}, "clientInfo": {"name": "data-agent", "version": "0.2.0"}})

    def send(self, method: str, params: dict) -> dict:
        self.request_id += 1
        body = json.dumps({"jsonrpc": "2.0", "id": self.request_id, "method": method, "params": params}).encode()
        headers = {"Content-Type": "application/json", "Accept": "text/event-stream, application/json", "X-API-Key": self.token}
        if self.session_id:
            headers["Mcp-Session-Id"] = self.session_id
        request = urllib.request.Request(self.url, data=body, headers=headers, method="POST")
        with urllib.request.urlopen(request, timeout=60) as response:
            self.session_id = response.headers.get("Mcp-Session-Id", self.session_id)
            text = response.read().decode("utf-8")
        candidates = [line[5:].strip() for line in text.splitlines() if line.startswith("data:")] or [text]
        for candidate in reversed(candidates):
            try:
                return json.loads(candidate)
            except json.JSONDecodeError:
                continue
        raise RuntimeError("无法解析 MCP 响应")

    def tool(self, name: str, arguments: dict) -> object:
        response = self.send("tools/call", {"name": name, "arguments": arguments})
        result = response.get("result", {})
        if result.get("isError"):
            raise RuntimeError("".join(str(x.get("text", "")) for x in result.get("content", [])))
        for item in result.get("content", []):
            if item.get("type") == "text":
                return json.loads(item["text"])
        return None


def metadata_call(server: str, action: str, database: str | None = None, table: str | None = None) -> object:
    client = MCPClient(server)
    arguments: dict[str, object] = {"dataSourceId": 1}
    tool_names = {
        "sources": "list_data_sources",
        "databases": "list_databases",
        "tables": "list_tables",
        "columns": "get_columns",
    }
    if database:
        arguments["database"] = database
    if table:
        arguments["table"] = table
    if action == "sources":
        arguments = {}
    return client.tool(tool_names[action], arguments)


def run_query(server: str, sql: str, output: Path, timeout: int) -> None:
    errors, warnings, _ = validate_sql(sql)
    if errors or warnings:
        for item in errors:
            print(f"ERROR: {item}", file=sys.stderr)
        for item in warnings:
            print(f"WARNING: {item}", file=sys.stderr)
    if errors:
        raise SystemExit(2)
    client = MCPClient(server)
    submitted = client.tool("submit_query", {"dataSourceId": 1, "engineType": "SMART", "sql": sql})
    query_id = submitted.get("queryId") if isinstance(submitted, dict) else submitted
    started = time.monotonic()
    while time.monotonic() - started < timeout:
        time.sleep(3)
        status_data = client.tool("get_query_status", {"queryId": query_id})
        status = status_data.get("status") if isinstance(status_data, dict) else str(status_data)
        print(f"query={query_id} status={status}")
        if status == "FINISHED":
            break
        if status == "FAILED":
            raise RuntimeError(str(status_data))
    else:
        raise TimeoutError(f"查询超过 {timeout} 秒")
    result = client.tool("get_query_result", {"queryId": query_id, "maxRows": 10000})
    columns = result.get("header", result.get("columns", result.get("headers", [])))
    names = [str(c.get("name", c.get("columnName"))) if isinstance(c, dict) else str(c) for c in columns]
    rows = result.get("rows", result.get("data", []))
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists():
        raise FileExistsError(f"拒绝覆盖已有文件：{output}")
    with output.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.writer(handle)
        writer.writerow(names)
        for row in rows:
            writer.writerow([row.get(name, "") for name in names] if isinstance(row, dict) else row)
    metadata = {"query_id": query_id, "rows": len(rows), "columns": names, "truncated": bool(result.get("truncated")), "created_at": datetime.now().astimezone().isoformat()}
    output.with_suffix(".meta.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"已保存 {len(rows)} 行：{output}")


def read_csv_preview(path: Path, limit: int = 200) -> tuple[list[str], list[list[str]], int]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.reader(handle)
        header = next(reader, [])
        rows = []
        total = 0
        for row in reader:
            total += 1
            if len(rows) < limit:
                rows.append(row)
    return header, rows, total


def inspect_csv(path: Path) -> dict:
    header, rows, total = read_csv_preview(path, 10000)
    issues: list[str] = []
    if not header:
        issues.append("缺少表头")
    if total == 0:
        issues.append("无数据")
    width = len(header)
    for index, row in enumerate(rows, start=2):
        if len(row) != width:
            issues.append(f"第 {index} 行列数不一致")
            if len(issues) >= 20:
                break
    for col_index, name in enumerate(header):
        lowered = name.lower()
        values = [row[col_index].strip() for row in rows if col_index < len(row) and row[col_index].strip()]
        if not values:
            continue
        numeric = []
        for value in values:
            try:
                numeric.append(float(value.rstrip("%")) / (100 if value.endswith("%") else 1))
            except ValueError:
                pass
        if len(numeric) == len(values):
            if any(token in lowered for token in ("rate", "ratio", "percent", "率", "占比")):
                if any(value < 0 or value > 1 for value in numeric):
                    issues.append(f"比率列 {name} 存在超出 0~1 的值")
            if any(token in lowered for token in ("amount", "principal", "balance", "金额", "本金", "余额")):
                if any(value < 0 for value in numeric):
                    issues.append(f"金额列 {name} 存在负值")
    meta = path.with_suffix(".meta.json")
    truncated = False
    if meta.exists():
        truncated = bool(json.loads(meta.read_text(encoding="utf-8")).get("truncated"))
        if truncated:
            issues.append("查询结果被截断")
    return {"file": str(path), "rows": total, "columns": header, "truncated": truncated, "issues": issues}


def init_project(project: Path, mode: str, request: str = "") -> None:
    if project.exists() and any(project.iterdir()):
        raise FileExistsError(f"项目目录非空：{project}")
    (project / "data").mkdir(parents=True, exist_ok=True)
    (project / "report").mkdir(parents=True, exist_ok=True)
    body = f"# 数据需求\n\n- 工作模式：{mode}\n- 原始需求：{request or '-'}\n- 时间范围：-\n- 指标：-\n- 维度：-\n- 筛选条件：-\n- 受众：-\n- 交付物：-\n"
    (project / "request.md").write_text(body, encoding="utf-8")


def project_stage(project: Path) -> dict:
    has_request = (project / "request.md").exists()
    has_sql = (project / "code.sql").exists()
    has_data = (project / "data").exists() and any((project / "data").glob("*.csv"))
    has_report = (project / "report" / "report.html").exists()
    if has_report:
        stage = "complete"
    elif has_data:
        stage = "report"
    elif has_sql:
        stage = "data"
    elif has_request:
        stage = "sql"
    else:
        stage = "request"
    return {"project": str(project), "stage": stage, "request": has_request, "sql": has_sql, "data": has_data, "report": has_report}


def build_report(project: Path) -> Path:
    spec_path = project / "report" / "report.json"
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    data_dir = project / "data"
    datasets = []
    for path in sorted(data_dir.glob("*.csv")):
        header, rows, total = read_csv_preview(path)
        datasets.append((path.name, header, rows, total))
    def p(text: object) -> str:
        return html.escape(str(text), quote=True)
    sections = []
    for section in spec.get("sections", []):
        source = section.get("source", "")
        if source and not (data_dir / source).exists():
            raise ValueError(f"报告引用不存在的数据文件：{source}")
        paragraphs = "".join(f"<p>{p(x)}</p>" for x in section.get("paragraphs", []))
        sections.append(f"<section><h2>{p(section.get('title',''))}</h2>{paragraphs}<div class='source'>来源：{p(source)}</div></section>")
    tables = []
    for name, header, rows, total in datasets:
        head = "".join(f"<th>{p(x)}</th>" for x in header)
        body = "".join("<tr>" + "".join(f"<td>{p(x)}</td>" for x in row) + "</tr>" for row in rows)
        tables.append(f"<section><h2>数据：{p(name)}</h2><p>共 {total} 行，展示前 {len(rows)} 行。</p><div class='table'><table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div></section>")
    summary = "".join(f"<li>{p(x)}</li>" for x in spec.get("summary", []))
    generated = datetime.now().astimezone().isoformat(timespec="seconds")
    document = f"""<!doctype html><html lang='zh-CN'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width'><title>{p(spec.get('title','数据分析报告'))}</title><style>body{{font:15px/1.65 system-ui;margin:0;background:#f5f7fb;color:#172033}}main{{max-width:1100px;margin:auto;padding:32px}}header,section{{background:white;margin:16px 0;padding:24px;border-radius:14px;box-shadow:0 4px 20px #17203312}}h1{{margin:0}}h2{{border-bottom:1px solid #e5e7eb;padding-bottom:8px}}.meta,.source{{color:#64748b;font-size:13px}}.table{{overflow:auto}}table{{border-collapse:collapse;width:100%}}th,td{{padding:8px 10px;border:1px solid #e5e7eb;text-align:left;white-space:nowrap}}th{{background:#f1f5f9}}</style></head><body><main><header><h1>{p(spec.get('title','数据分析报告'))}</h1><div class='meta'>报告日期：{p(spec.get('date',''))} ｜ 分析师：{p(spec.get('analyst',''))} ｜ 生成：{p(generated)}</div></header><section><h2>核心结论</h2><ul>{summary}</ul></section>{''.join(sections)}{''.join(tables)}<section><h2>数据与口径</h2><p>SQL：{p('code.sql' if (project/'code.sql').exists() else '未提供')}</p><p>数据文件：{p(', '.join(x[0] for x in datasets))}</p></section></main></body></html>"""
    output = project / "report" / "report.html"
    output.write_text(document, encoding="utf-8")
    print(f"报告已生成：{output}")
    return output


def audit_project(project: Path) -> int:
    issues: list[str] = []
    for required in ("request.md", "code.sql"):
        if not (project / required).exists():
            issues.append(f"缺少 {required}")
    sql_path = project / "code.sql"
    if sql_path.exists():
        errors, warnings, _ = validate_sql(sql_path.read_text(encoding="utf-8"))
        issues.extend("SQL错误：" + x for x in errors)
        issues.extend("SQL警告：" + x for x in warnings)
    csv_files = list((project / "data").glob("*.csv")) if (project / "data").exists() else []
    if not csv_files:
        issues.append("缺少 data/*.csv")
    for path in csv_files:
        header, _, total = read_csv_preview(path, 1)
        if not header:
            issues.append(f"{path.name} 缺少表头")
        if total == 0:
            issues.append(f"{path.name} 无数据")
        meta_path = path.with_suffix(".meta.json")
        if meta_path.exists() and json.loads(meta_path.read_text(encoding="utf-8")).get("truncated"):
            issues.append(f"{path.name} 查询结果被截断")
    report_spec = project / "report" / "report.json"
    if report_spec.exists():
        spec = json.loads(report_spec.read_text(encoding="utf-8"))
        for section in spec.get("sections", []):
            source = section.get("source")
            if source and not (project / "data" / source).exists():
                issues.append(f"报告引用不存在的数据文件：{source}")
    else:
        issues.append("缺少 report/report.json")
    if issues:
        print("审计未通过：")
        for issue in issues:
            print(f"- {issue}")
        return 1
    print("审计通过")
    return 0


def audit_scope(project: Path, scope: str) -> tuple[str, list[str]]:
    issues: list[str] = []
    warnings: list[str] = []
    request_path = project / "request.md"
    sql_path = project / "code.sql"
    csv_files = sorted((project / "data").glob("*.csv")) if (project / "data").exists() else []
    if scope in {"request", "project"} and not request_path.exists():
        issues.append("缺少 request.md")
    if scope in {"sql", "request", "project"}:
        if not sql_path.exists():
            issues.append("缺少 code.sql")
        else:
            errors, sql_warnings, tables = validate_sql(sql_path.read_text(encoding="utf-8"))
            issues.extend(errors)
            warnings.extend(sql_warnings)
            for table in tables:
                matches = search_index(table, "hive", 1) if INDEX.exists() else []
                if INDEX.exists() and not matches:
                    warnings.append(f"知识库未找到表：{table}")
    if scope in {"data", "report", "project"}:
        if not csv_files:
            issues.append("缺少 data/*.csv")
        for path in csv_files:
            result = inspect_csv(path)
            issues.extend(f"{path.name}: {item}" for item in result["issues"] if item in {"无数据", "缺少表头"})
            warnings.extend(f"{path.name}: {item}" for item in result["issues"] if item not in {"无数据", "缺少表头"})
    if scope in {"report", "project"}:
        spec_path = project / "report" / "report.json"
        if not spec_path.exists():
            issues.append("缺少 report/report.json")
        else:
            spec = json.loads(spec_path.read_text(encoding="utf-8"))
            if not spec.get("summary"):
                warnings.append("报告缺少核心结论")
            for section in spec.get("sections", []):
                source = section.get("source")
                if source and not (project / "data" / source).exists():
                    issues.append(f"报告引用不存在的数据文件：{source}")
    status = "blocked" if issues else "warning" if warnings else "passed"
    return status, issues + warnings


def main() -> int:
    parser = argparse.ArgumentParser(prog="data-agent")
    commands = parser.add_subparsers(dest="group", required=True)
    knowledge = commands.add_parser("knowledge").add_subparsers(dest="action", required=True)
    kb_build = knowledge.add_parser("build")
    kb_build.add_argument("--knowledge-dir", type=Path, default=KNOWLEDGE)
    kb_status = knowledge.add_parser("status")
    kb_status.add_argument("--knowledge-dir", type=Path, default=KNOWLEDGE)
    kb_import = knowledge.add_parser("import")
    kb_import.add_argument("--zip", type=Path, required=True)
    kb_import.add_argument("--replace", action="store_true")
    kb_search = knowledge.add_parser("search")
    kb_search.add_argument("query")
    kb_search.add_argument("--scope", choices=["all", *SOURCES], default="all")
    kb_search.add_argument("--limit", type=int, default=12)
    kb_search.add_argument("--grouped", action="store_true")
    kb_read = knowledge.add_parser("read")
    kb_read.add_argument("--scope", choices=list(SOURCES), required=True)
    kb_read.add_argument("--path", required=True)
    kb_aliases = knowledge.add_parser("aliases")
    kb_aliases.add_argument("query")
    kb_trace = knowledge.add_parser("trace")
    kb_trace.add_argument("query")
    sql_group = commands.add_parser("sql").add_subparsers(dest="action", required=True)
    sql_validate = sql_group.add_parser("validate")
    sql_validate.add_argument("--file", type=Path, required=True)
    query_group = commands.add_parser("query").add_subparsers(dest="action", required=True)
    query_run = query_group.add_parser("run")
    query_run.add_argument("--server", choices=["cn", "id"], required=True)
    query_run.add_argument("--sql-file", type=Path, required=True)
    query_run.add_argument("--output", type=Path, required=True)
    query_run.add_argument("--timeout", type=int, default=600)
    metadata_group = commands.add_parser("metadata").add_subparsers(dest="action", required=True)
    for action in ("sources", "databases", "tables", "columns"):
        item = metadata_group.add_parser(action)
        item.add_argument("--server", choices=["cn", "id"], required=True)
        if action in {"tables", "columns"}:
            item.add_argument("--database", required=True)
        if action == "columns":
            item.add_argument("--table", required=True)
    data_group = commands.add_parser("data").add_subparsers(dest="action", required=True)
    data_check = data_group.add_parser("check")
    data_check.add_argument("--file", type=Path, required=True)
    report_group = commands.add_parser("report").add_subparsers(dest="action", required=True)
    report_build = report_group.add_parser("build")
    report_build.add_argument("--project", type=Path, required=True)
    audit_group = commands.add_parser("audit").add_subparsers(dest="action", required=True)
    audit_project_parser = audit_group.add_parser("project")
    audit_project_parser.add_argument("--project", type=Path, required=True)
    audit_check = audit_group.add_parser("check")
    audit_check.add_argument("--project", type=Path, required=True)
    audit_check.add_argument("--scope", choices=["request", "sql", "data", "report", "project"], required=True)
    project_group = commands.add_parser("project").add_subparsers(dest="action", required=True)
    project_init = project_group.add_parser("init")
    project_init.add_argument("--project", type=Path, required=True)
    project_init.add_argument("--mode", choices=["analysis", "extract", "explore", "report", "sql-fix", "knowledge"], required=True)
    project_init.add_argument("--request", default="")
    project_stage_parser = project_group.add_parser("stage")
    project_stage_parser.add_argument("--project", type=Path, required=True)
    args = parser.parse_args()
    if args.group == "knowledge" and args.action == "build":
        build_index(args.knowledge_dir)
    elif args.group == "knowledge" and args.action == "status":
        print(json.dumps(knowledge_status(args.knowledge_dir), ensure_ascii=False, indent=2))
    elif args.group == "knowledge" and args.action == "import":
        count = import_knowledge(args.zip, replace=args.replace)
        print(json.dumps({"imported": count, "knowledge_dir": str(KNOWLEDGE)}, ensure_ascii=False, indent=2))
    elif args.group == "knowledge" and args.action == "search":
        result = search_grouped(args.query, args.scope, min(args.limit, 20)) if args.grouped else search_index(args.query, args.scope, args.limit)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    elif args.group == "knowledge" and args.action == "read":
        print(resolve_document(args.scope, args.path).read_text(encoding="utf-8-sig"))
    elif args.group == "knowledge" and args.action == "aliases":
        print(json.dumps(lookup_aliases(args.query), ensure_ascii=False, indent=2))
    elif args.group == "knowledge" and args.action == "trace":
        print(json.dumps(trace_knowledge(args.query), ensure_ascii=False, indent=2))
    elif args.group == "sql" and args.action == "validate":
        errors, warnings, tables = validate_sql(args.file.read_text(encoding="utf-8"))
        print(json.dumps({"valid": not errors, "errors": errors, "warnings": warnings, "tables": tables}, ensure_ascii=False, indent=2))
        return 1 if errors else 0
    elif args.group == "query" and args.action == "run":
        run_query(args.server, args.sql_file.read_text(encoding="utf-8"), args.output, args.timeout)
    elif args.group == "metadata":
        result = metadata_call(args.server, args.action, getattr(args, "database", None), getattr(args, "table", None))
        print(json.dumps(result, ensure_ascii=False, indent=2))
    elif args.group == "data" and args.action == "check":
        result = inspect_csv(args.file)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 1 if result["issues"] else 0
    elif args.group == "report" and args.action == "build":
        build_report(args.project)
    elif args.group == "audit" and args.action == "project":
        return audit_project(args.project)
    elif args.group == "audit" and args.action == "check":
        status, findings = audit_scope(args.project, args.scope)
        print(json.dumps({"status": status, "scope": args.scope, "findings": findings}, ensure_ascii=False, indent=2))
        return 1 if status == "blocked" else 0
    elif args.group == "project" and args.action == "init":
        init_project(args.project, args.mode, args.request)
        print(json.dumps(project_stage(args.project), ensure_ascii=False, indent=2))
    elif args.group == "project" and args.action == "stage":
        print(json.dumps(project_stage(args.project), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
