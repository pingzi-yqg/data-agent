import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).parents[1] / ".agents/skills/data-agent/scripts/data_agent.py"
SPEC = importlib.util.spec_from_file_location("data_agent_cli", SCRIPT)
data_agent = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(data_agent)


class SqlGuardTests(unittest.TestCase):
    def test_valid_snapshot_query(self):
        sql = "SELECT product, count(*) n FROM analytics.application_df_daily WHERE dt='20260920' GROUP BY product LIMIT 100"
        errors, warnings, tables = data_agent.validate_sql(sql)
        self.assertEqual([], errors)
        self.assertEqual([], warnings)
        self.assertEqual(["analytics.application_df_daily"], tables)

    def test_rejects_dml(self):
        errors, _, _ = data_agent.validate_sql("DELETE FROM dm.orders WHERE id=1")
        self.assertTrue(errors)

    def test_rejects_multiple_statements(self):
        errors, _, _ = data_agent.validate_sql("SELECT 1 LIMIT 1; SELECT 2 LIMIT 1")
        self.assertIn("只允许一条 SQL 语句", errors)

    def test_rejects_sensitive_table(self):
        errors, _, _ = data_agent.validate_sql("SELECT id FROM corp.salary_detail LIMIT 10")
        self.assertTrue(any("敏感数据源" in error for error in errors))

    def test_requires_snapshot_partition(self):
        errors, _, _ = data_agent.validate_sql("SELECT id FROM analytics.application_df_daily LIMIT 10")
        self.assertTrue(any("dt" in error for error in errors))


class ReportTests(unittest.TestCase):
    def test_report_escapes_untrusted_values(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            (project / "data").mkdir()
            (project / "report").mkdir()
            (project / "code.sql").write_text("SELECT 1 LIMIT 1", encoding="utf-8")
            (project / "data/result.csv").write_text("name,value\n<script>alert(1)</script>,1\n", encoding="utf-8")
            spec = {"title": "<b>标题</b>", "summary": ["<img src=x>"], "sections": [{"title": "结果", "paragraphs": ["安全"], "source": "result.csv"}]}
            (project / "report/report.json").write_text(json.dumps(spec, ensure_ascii=False), encoding="utf-8")
            output = data_agent.build_report(project)
            content = output.read_text(encoding="utf-8")
            self.assertNotIn("<script>alert(1)</script>", content)
            self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", content)
            self.assertIn("&lt;b&gt;标题&lt;/b&gt;", content)


class ExtendedWorkflowTests(unittest.TestCase):
    def test_project_init_and_stage(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory) / "project"
            data_agent.init_project(project, "knowledge", "查询转化率口径")
            status = data_agent.project_stage(project)
            self.assertEqual("sql", status["stage"])
            self.assertTrue((project / "data").is_dir())
            self.assertTrue((project / "report").is_dir())

    def test_csv_quality_check(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "result.csv"
            path.write_text("name,rate,amount\na,1.2,-3\n", encoding="utf-8")
            result = data_agent.inspect_csv(path)
            self.assertTrue(any("比率列" in item for item in result["issues"]))
            self.assertTrue(any("金额列" in item for item in result["issues"]))

    def test_rejects_zip_path_traversal(self):
        with tempfile.TemporaryDirectory() as directory:
            archive = Path(directory) / "bad.zip"
            import zipfile
            with zipfile.ZipFile(archive, "w") as handle:
                handle.writestr("../escape.txt", "bad")
            with self.assertRaises(ValueError):
                data_agent.import_knowledge(archive, Path(directory) / "knowledge")


if __name__ == "__main__":
    unittest.main()
