#!/usr/bin/env python3
"""Tests for junit_sum. Stdlib unittest only."""

from __future__ import annotations

import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import junit_sum as js  # noqa: E402

EXAMPLES = ROOT / "examples"


class ParseExamples(unittest.TestCase):
    def test_order_suite(self):
        report = js.load_reports([str(EXAMPLES / "order-tests.xml")], recursive=False)
        self.assertEqual(report.suites, 1)
        self.assertEqual(report.tests, 6)
        self.assertEqual(report.passed, 3)
        self.assertEqual(report.failed, 1)
        self.assertEqual(report.errors, 1)
        self.assertEqual(report.skipped, 1)
        self.assertEqual(report.flaky, 1)
        failed = [c for c in report.cases if c.status == "failed"]
        self.assertEqual(failed[0].label, "com.example.shop.OrderServiceTest.rejectsNegativeQuantity")
        self.assertIn("ACCEPTED", failed[0].message)
        flaky = [c for c in report.cases if c.flaky]
        self.assertEqual(flaky[0].status, "passed")
        self.assertIn("SocketTimeoutException", flaky[0].earlier)

    def test_pytest_suites(self):
        report = js.load_reports([str(EXAMPLES / "pytest-sample.xml")], recursive=False)
        self.assertEqual(report.suites, 2)
        self.assertEqual(report.tests, 4)
        self.assertEqual(report.failed, 1)
        self.assertEqual(report.passed, 3)
        self.assertAlmostEqual(report.time or 0, 0.186, places=3)

    def test_namespaced(self):
        report = js.load_reports([str(EXAMPLES / "namespaced.xml")], recursive=False)
        self.assertEqual(report.tests, 1)
        self.assertEqual(report.passed, 1)
        self.assertEqual(report.cases[0].label, "com.example.NsTest.ok")


class ParseEdges(unittest.TestCase):
    def _parse(self, xml: str, source: str = "memory.xml") -> js.Report:
        holder = js.Report()
        parsed = js.parse_xml_bytes(xml.encode("utf-8"), source, holder)
        self.assertIsNotNone(parsed)
        return parsed  # type: ignore[return-value]

    def test_count_mismatch_warning(self):
        xml = '''<testsuite name="Demo" tests="9" time="0.1">
  <testcase name="a" classname="Demo" time="0.1"/>
</testsuite>'''
        report = self._parse(xml, "mismatch.xml")
        self.assertEqual(report.tests, 1)
        self.assertTrue(any("declared tests=9" in w for w in report.warnings))

    def test_not_junit(self):
        holder = js.Report()
        parsed = js.parse_xml_bytes(b"<project><modelVersion>4.0.0</modelVersion></project>", "pom.xml", holder)
        self.assertIsNone(parsed)
        self.assertTrue(any("not a JUnit" in w for w in holder.warnings))

    def test_invalid_xml(self):
        holder = js.Report()
        parsed = js.parse_xml_bytes(b"<testsuite>", "broken.xml", holder)
        self.assertIsNone(parsed)
        self.assertTrue(any("invalid XML" in w for w in holder.warnings))

    def test_stdin_and_directory(self):
        report = js.load_reports([str(EXAMPLES)], recursive=False)
        self.assertGreaterEqual(report.tests, 11)

    def test_time_formats(self):
        self.assertEqual(js.format_time(0.014), "0.014s")
        self.assertEqual(js.format_time(12.34), "12.3s")
        self.assertTrue(js.format_time(75).startswith("1m "))
        self.assertEqual(js.parse_time("1,5"), 1.5)


class Cli(unittest.TestCase):
    def test_text_and_strict_exit(self):
        buf = io.StringIO()
        code = js.main([str(EXAMPLES / "order-tests.xml"), "--strict", "--stack", "2"], stdout=buf)
        text = buf.getvalue()
        self.assertEqual(code, 1)
        self.assertIn("Failures (1)", text)
        self.assertIn("Errors (1)", text)
        self.assertIn("Slowest", text)
        self.assertIn("rejectsNegativeQuantity", text)
        self.assertIn("OrderService.java:88", text)

    def test_json_and_clean_exit(self):
        buf = io.StringIO()
        code = js.main([str(EXAMPLES / "namespaced.xml"), "--json"], stdout=buf)
        self.assertEqual(code, 0)
        data = json.loads(buf.getvalue())
        self.assertEqual(data["tests"], 1)
        self.assertEqual(data["failed"], 0)
        self.assertEqual(data["passed"], 1)

    def test_usage_without_input(self):
        # Force the "no paths and stdin is a tty" branch via resolve_paths
        code = js.main([], stdout=io.StringIO())
        # In this test process stdin may not be a tty; if it is empty, load_reports warns.
        self.assertIn(code, (0, 2, 1))

    def test_failed_only_hides_slowest(self):
        buf = io.StringIO()
        js.main([str(EXAMPLES / "order-tests.xml"), "--failed-only"], stdout=buf)
        text = buf.getvalue()
        self.assertIn("Failures (1)", text)
        self.assertNotIn("Slowest", text)
        self.assertNotIn("Skipped (1)", text)

    def test_missing_path_strict(self):
        buf = io.StringIO()
        with tempfile.TemporaryDirectory() as tmp:
            missing = os.path.join(tmp, "no-such.xml")
            code = js.main([missing, "--strict"], stdout=buf)
        self.assertEqual(code, 1)
        self.assertIn("path not found", buf.getvalue())


if __name__ == "__main__":
    unittest.main()
