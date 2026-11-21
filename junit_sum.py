#!/usr/bin/env python3
"""Summarize JUnit / Surefire / xUnit XML reports. Stdlib only."""

from __future__ import annotations

import argparse
import json
import os
import sys
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field, asdict
from typing import Any, Iterable, List, Optional, Sequence, TextIO

USAGE_EXIT = 2
FAIL_EXIT = 1
MAX_BYTES = 50 * 1024 * 1024

FAILURE_TAGS = frozenset({"failure"})
ERROR_TAGS = frozenset({"error"})
SKIP_TAGS = frozenset({"skipped", "ignored"})
RERUN_TAGS = frozenset({"rerunFailure", "rerunError", "flakyFailure", "flakyError"})
IGNORE_CHILD_TAGS = frozenset({"system-out", "system-err", "properties", "property"})


def local_tag(tag: Optional[str]) -> str:
    if not tag:
        return ""
    if tag.startswith("{") and "}" in tag:
        return tag.rsplit("}", 1)[-1]
    return tag


def parse_time(value: Optional[str]) -> Optional[float]:
    if value is None:
        return None
    text = value.strip().replace(",", ".")
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def format_time(seconds: Optional[float]) -> str:
    if seconds is None:
        return "-"
    if seconds < 0:
        seconds = 0.0
    if seconds < 10:
        return "%.3fs" % seconds
    if seconds < 60:
        return "%.1fs" % seconds
    minutes, rem = divmod(seconds, 60)
    if minutes < 60:
        return "%dm %04.1fs" % (int(minutes), rem)
    hours, minutes = divmod(int(minutes), 60)
    return "%dh %dm %04.1fs" % (hours, minutes, rem)


def first_lines(text: str, n: int) -> str:
    if n <= 0 or not text:
        return ""
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    # drop leading/trailing empty lines, keep a compact stack
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()
    if len(lines) <= n:
        return "\n".join(lines)
    return "\n".join(lines[:n]) + "\n..."


def compact_message(text: Optional[str], limit: int = 240) -> str:
    if not text:
        return ""
    one = " ".join(text.split())
    if len(one) <= limit:
        return one
    return one[: limit - 3] + "..."


def qualified_name(classname: str, name: str) -> str:
    classname = (classname or "").strip()
    name = (name or "").strip()
    if classname and name:
        if name.startswith(classname + ".") or name.startswith(classname + "#"):
            return name
        return "%s.%s" % (classname, name)
    return name or classname or "(unnamed)"


@dataclass
class CaseResult:
    name: str
    classname: str
    status: str  # passed | failed | error | skipped
    time: Optional[float] = None
    message: str = ""
    typ: str = ""
    stack: str = ""
    flaky: bool = False
    earlier: str = ""
    source: str = ""
    suite: str = ""

    @property
    def label(self) -> str:
        return qualified_name(self.classname, self.name)


@dataclass
class Report:
    sources: List[str] = field(default_factory=list)
    suites: int = 0
    cases: List[CaseResult] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    declared_tests: Optional[int] = None
    declared_time: Optional[float] = None

    def add_warning(self, msg: str) -> None:
        if msg not in self.warnings:
            self.warnings.append(msg)

    @property
    def tests(self) -> int:
        return len(self.cases)

    def count(self, status: str) -> int:
        return sum(1 for c in self.cases if c.status == status)

    @property
    def passed(self) -> int:
        return self.count("passed")

    @property
    def failed(self) -> int:
        return self.count("failed")

    @property
    def errors(self) -> int:
        return self.count("error")

    @property
    def skipped(self) -> int:
        return self.count("skipped")

    @property
    def flaky(self) -> int:
        return sum(1 for c in self.cases if c.flaky)

    @property
    def time(self) -> Optional[float]:
        times = [c.time for c in self.cases if c.time is not None]
        if times:
            return sum(times)
        return self.declared_time

    def extend(self, other: "Report") -> None:
        self.sources.extend(other.sources)
        self.suites += other.suites
        self.cases.extend(other.cases)
        for w in other.warnings:
            self.add_warning(w)
        if other.declared_tests is not None:
            self.declared_tests = (self.declared_tests or 0) + other.declared_tests
        if other.declared_time is not None:
            self.declared_time = (self.declared_time or 0.0) + other.declared_time


def child_by_tags(elem: ET.Element, tags: Iterable[str]) -> List[ET.Element]:
    wanted = set(tags)
    return [c for c in list(elem) if local_tag(c.tag) in wanted]


def text_of(elem: ET.Element) -> str:
    parts = []
    if elem.text and elem.text.strip():
        parts.append(elem.text.strip())
    for child in list(elem):
        if child.tail and child.tail.strip():
            parts.append(child.tail.strip())
    return "\n".join(parts)


def detail_from(nodes: Sequence[ET.Element]) -> tuple[str, str, str]:
    if not nodes:
        return "", "", ""
    node = nodes[0]
    typ = (node.get("type") or "").strip()
    message = (node.get("message") or "").strip()
    body = text_of(node)
    if not message and body:
        message = body.splitlines()[0].strip()
    return typ, message, body


def parse_case(elem: ET.Element, source: str, suite_name: str) -> CaseResult:
    name = elem.get("name") or elem.get("methodname") or ""
    classname = elem.get("classname") or elem.get("class") or suite_name
    case = CaseResult(
        name=name,
        classname=classname or "",
        status="passed",
        time=parse_time(elem.get("time")),
        source=source,
        suite=suite_name,
    )
    failures = child_by_tags(elem, FAILURE_TAGS)
    errors = child_by_tags(elem, ERROR_TAGS)
    skipped = child_by_tags(elem, SKIP_TAGS)
    reruns = child_by_tags(elem, RERUN_TAGS)

    if errors:
        typ, message, body = detail_from(errors)
        case.status = "error"
        case.typ, case.message, case.stack = typ, message, body
    elif failures:
        typ, message, body = detail_from(failures)
        case.status = "failed"
        case.typ, case.message, case.stack = typ, message, body
    elif skipped:
        typ, message, body = detail_from(skipped)
        case.status = "skipped"
        case.typ, case.message, case.stack = typ, message, body

    if reruns:
        case.flaky = True
        rtyp, rmsg, rbody = detail_from(reruns)
        bits = [x for x in (rtyp, compact_message(rmsg or rbody, 200)) if x]
        case.earlier = " ".join(bits)
        if case.status == "passed" and not case.message:
            case.message = "passed on retry"
    return case


def find_suites(root: ET.Element) -> List[ET.Element]:
    out: List[ET.Element] = []
    if local_tag(root.tag) == "testsuite":
        out.append(root)
    for elem in root.iter():
        if local_tag(elem.tag) == "testsuite" and elem is not root:
            out.append(elem)
        elif local_tag(elem.tag) == "testsuite" and elem is root:
            continue
    return out


def declared_counts(root: ET.Element) -> tuple[Optional[int], Optional[float]]:
    tag = local_tag(root.tag)
    if tag in {"testsuites", "testsuite"}:
        tests = root.get("tests")
        time = parse_time(root.get("time"))
        try:
            n = int(tests) if tests is not None and tests.strip() != "" else None
        except ValueError:
            n = None
        return n, time
    return None, None


def parse_tree(root: ET.Element, source: str) -> Optional[Report]:
    tag = local_tag(root.tag)
    if tag not in {"testsuite", "testsuites"}:
        # some publishers wrap the suite; search one level down
        children = [c for c in list(root) if local_tag(c.tag) in {"testsuite", "testsuites"}]
        if len(children) == 1:
            return parse_tree(children[0], source)
        return None

    report = Report(sources=[source])
    declared_tests, declared_time = declared_counts(root)
    report.declared_tests = declared_tests
    report.declared_time = declared_time

    suites = find_suites(root)
    report.suites = len(suites) if suites else (1 if tag == "testsuite" else 0)

    seen: set[int] = set()
    if not suites and tag == "testsuites":
        # no nested testsuite, but maybe bare testcase children
        suites = [root]

    for suite in suites:
        suite_name = (suite.get("name") or suite.get("package") or "").strip()
        for elem in suite.iter():
            if local_tag(elem.tag) != "testcase":
                continue
            ident = id(elem)
            if ident in seen:
                continue
            seen.add(ident)
            report.cases.append(parse_case(elem, source, suite_name))

    if report.declared_tests is not None and report.declared_tests != report.tests:
        report.add_warning(
            "%s: declared tests=%s but found %d testcase element(s)"
            % (source, report.declared_tests, report.tests)
        )
    return report


def read_bytes(path: str) -> bytes:
    if path == "-":
        data = sys.stdin.buffer.read()
        return data
    with open(path, "rb") as fh:
        return fh.read(MAX_BYTES + 1)


def parse_xml_bytes(data: bytes, source: str, report: Report) -> Optional[Report]:
    if len(data) > MAX_BYTES:
        report.add_warning("%s: larger than %d bytes, skipped" % (source, MAX_BYTES))
        return None
    if not data.strip():
        report.add_warning("%s: empty file, skipped" % source)
        return None
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = data.decode("utf-8", errors="replace")
        report.add_warning("%s: not valid UTF-8, replaced invalid bytes" % source)
    try:
        root = ET.fromstring(text)
    except ET.ParseError as exc:
        report.add_warning("%s: invalid XML (%s)" % (source, exc))
        return None
    parsed = parse_tree(root, source)
    if parsed is None:
        report.add_warning("%s: not a JUnit / xUnit report, skipped" % source)
        return None
    return parsed


def is_xml_path(path: str) -> bool:
    return path.lower().endswith(".xml")


def collect_paths(paths: Sequence[str], recursive: bool) -> tuple[List[str], List[str]]:
    files: List[str] = []
    warnings: List[str] = []
    for raw in paths:
        if raw == "-":
            files.append("-")
            continue
        if not os.path.exists(raw):
            warnings.append("%s: path not found" % raw)
            continue
        if os.path.isfile(raw):
            files.append(raw)
            continue
        if os.path.isdir(raw):
            found: List[str] = []
            if recursive:
                for root, dirnames, names in os.walk(raw):
                    dirnames.sort()
                    for name in sorted(names):
                        if is_xml_path(name):
                            found.append(os.path.join(root, name))
            else:
                try:
                    names = os.listdir(raw)
                except OSError as exc:
                    warnings.append("%s: cannot list directory (%s)" % (raw, exc))
                    continue
                for name in sorted(names):
                    p = os.path.join(raw, name)
                    if os.path.isfile(p) and is_xml_path(name):
                        found.append(p)
            if not found:
                warnings.append("%s: no .xml files found" % raw)
            files.extend(found)
            continue
        warnings.append("%s: not a file or directory" % raw)
    # stable unique, keep first occurrence
    uniq: List[str] = []
    seen = set()
    for p in files:
        key = p if p == "-" else os.path.abspath(p)
        if key in seen:
            continue
        seen.add(key)
        uniq.append(p)
    return uniq, warnings


def load_reports(paths: Sequence[str], recursive: bool) -> Report:
    report = Report()
    files, warnings = collect_paths(paths, recursive)
    for w in warnings:
        report.add_warning(w)
    if not files:
        report.add_warning("no input files")
        return report
    for path in files:
        source = "stdin" if path == "-" else path
        try:
            data = read_bytes(path)
        except OSError as exc:
            report.add_warning("%s: cannot read (%s)" % (source, exc))
            continue
        parsed = parse_xml_bytes(data, source, report)
        if parsed is not None:
            report.extend(parsed)
    report.sources = list(dict.fromkeys(report.sources))
    return report


def case_payload(case: CaseResult, stack_lines: int) -> dict[str, Any]:
    payload = {
        "name": case.name,
        "classname": case.classname,
        "label": case.label,
        "status": case.status,
        "time": case.time,
        "type": case.typ or None,
        "message": compact_message(case.message) or None,
        "flaky": case.flaky,
        "earlier": case.earlier or None,
        "source": case.source,
        "suite": case.suite or None,
    }
    stack = first_lines(case.stack, stack_lines)
    payload["stack"] = stack or None
    return payload


def to_json(report: Report, slow: int, stack_lines: int, failed_only: bool) -> dict[str, Any]:
    failures = [c for c in report.cases if c.status == "failed"]
    errors = [c for c in report.cases if c.status == "error"]
    skipped = [c for c in report.cases if c.status == "skipped"]
    flaky = [c for c in report.cases if c.flaky]
    slowest = slowest_cases(report.cases, slow)
    data: dict[str, Any] = {
        "sources": report.sources,
        "suites": report.suites,
        "tests": report.tests,
        "passed": report.passed,
        "failed": report.failed,
        "errors": report.errors,
        "skipped": report.skipped,
        "flaky": report.flaky,
        "time": report.time,
        "failures": [case_payload(c, stack_lines) for c in failures],
        "error_cases": [case_payload(c, stack_lines) for c in errors],
        "skipped_cases": [case_payload(c, stack_lines) for c in skipped],
        "flaky_cases": [case_payload(c, stack_lines) for c in flaky],
        "warnings": report.warnings,
    }
    if not failed_only:
        data["slowest"] = [case_payload(c, 0) for c in slowest]
    return data


def slowest_cases(cases: Sequence[CaseResult], n: int) -> List[CaseResult]:
    if n <= 0:
        return []
    timed = [c for c in cases if c.time is not None]
    timed.sort(key=lambda c: (-(c.time or 0.0), c.label))
    return timed[:n]



def stack_starts_with_detail(first: str, typ: str, message: str) -> bool:
    first = (first or "").strip()
    typ = (typ or "").strip()
    msg = compact_message(message)
    if not first:
        return False
    if typ and first == typ:
        return True
    if msg and (first == msg or first.startswith(msg) or first.endswith(msg)):
        return True
    if typ and msg:
        raw = (message or "").strip()
        for cand in ("%s: %s" % (typ, raw), "%s %s" % (typ, msg), "%s: %s" % (typ, msg)):
            cand = cand.strip()
            if first == cand or first.startswith(cand):
                return True
    return False


def emit_section(title: str, cases: Sequence[CaseResult], stack_lines: int, show_time: bool = True) -> List[str]:
    if not cases:
        return []
    lines = ["", "%s (%d)" % (title, len(cases))]
    for case in cases:
        timing = "  (%s)" % format_time(case.time) if show_time and case.time is not None else ""
        lines.append("  %s%s" % (case.label, timing))
        typ = (case.typ or "").strip()
        msg = compact_message(case.message)
        if typ and msg:
            head = "%s: %s" % (typ, msg)
        else:
            head = typ or msg
        if case.status == "passed" and case.flaky and case.earlier:
            lines.append("    earlier: %s" % case.earlier)
            if head:
                lines.append("    %s" % head)
        elif head:
            lines.append("    %s" % head)
        elif case.status == "skipped" and not head:
            lines.append("    (no reason given)")
        stack = first_lines(case.stack, stack_lines)
        if stack and case.status in {"failed", "error"}:
            # avoid repeating the message line if the stack starts with it
            body_lines = stack.splitlines()
            if body_lines and stack_starts_with_detail(body_lines[0], case.typ, case.message):
                body_lines = body_lines[1:]
            for sl in body_lines:
                lines.append("    %s" % sl)
    return lines


def render_text(report: Report, slow: int, stack_lines: int, failed_only: bool) -> str:
    sources = report.sources or ["(none)"]
    lines: List[str] = []
    lines.append("Source:     %s" % sources[0])
    for extra in sources[1:]:
        lines.append("            %s" % extra)
    lines.append("Suites:     %d" % report.suites)
    parts = [
        str(report.tests),
        "passed %d" % report.passed,
        "failed %d" % report.failed,
        "errors %d" % report.errors,
        "skipped %d" % report.skipped,
    ]
    if report.flaky:
        parts.append("flaky %d" % report.flaky)
    lines.append("Tests:      " + "   ".join(parts))
    lines.append("Time:       %s" % format_time(report.time))
    if report.warnings:
        lines.append("Warnings")
        for w in report.warnings:
            lines.append("  %s" % w)

    failures = [c for c in report.cases if c.status == "failed"]
    errors = [c for c in report.cases if c.status == "error"]
    skipped = [c for c in report.cases if c.status == "skipped"]
    flaky_pass = [c for c in report.cases if c.flaky and c.status == "passed"]

    lines.extend(emit_section("Failures", failures, stack_lines))
    lines.extend(emit_section("Errors", errors, stack_lines))
    if not failed_only:
        lines.extend(emit_section("Skipped", skipped, 0))
        lines.extend(emit_section("Flaky (passed on retry)", flaky_pass, 0))
        slowest = slowest_cases(report.cases, slow)
        if slowest:
            lines.append("")
            lines.append("Slowest")
            for case in slowest:
                mark = ""
                if case.status == "failed":
                    mark = "  [failed]"
                elif case.status == "error":
                    mark = "  [error]"
                elif case.status == "skipped":
                    mark = "  [skipped]"
                elif case.flaky:
                    mark = "  [flaky]"
                lines.append("  %s  %s%s" % (format_time(case.time).rjust(10), case.label, mark))
    return "\n".join(lines) + "\n"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="junit-sum",
        description="Summarize JUnit / Surefire / xUnit XML reports locally (stdlib only).",
    )
    parser.add_argument(
        "paths",
        nargs="*",
        help="XML files or directories; use - to read stdin",
    )
    parser.add_argument("-r", "--recursive", action="store_true", help="scan directories recursively")
    parser.add_argument("--json", action="store_true", dest="as_json", help="print machine-readable JSON")
    parser.add_argument(
        "--slow",
        type=int,
        default=5,
        metavar="N",
        help="how many slowest tests to list (default: 5; 0 to hide)",
    )
    parser.add_argument(
        "--stack",
        type=int,
        default=5,
        metavar="N",
        help="stack lines to show per failure (default: 5; 0 to hide)",
    )
    parser.add_argument(
        "--failed-only",
        action="store_true",
        help="only list failures and errors (hide skipped, flaky, slowest)",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="exit 1 if any failure, error, unreadable input, or count mismatch warning",
    )
    return parser


def resolve_paths(paths: Sequence[str]) -> List[str]:
    if paths:
        return list(paths)
    if not sys.stdin.isatty():
        return ["-"]
    return []


def main(argv: Optional[Sequence[str]] = None, stdout: Optional[TextIO] = None) -> int:
    out = stdout or sys.stdout
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    if args.slow < 0 or args.stack < 0:
        parser.error("--slow and --stack must be >= 0")
    paths = resolve_paths(args.paths)
    if not paths:
        parser.print_usage(sys.stderr)
        sys.stderr.write("junit-sum: provide an XML file/directory, or pipe a report to stdin\n")
        return USAGE_EXIT

    report = load_reports(paths, args.recursive)
    if args.as_json:
        json.dump(to_json(report, args.slow, args.stack, args.failed_only), out, ensure_ascii=False, indent=2)
        out.write("\n")
    else:
        out.write(render_text(report, args.slow, args.stack, args.failed_only))

    has_problem = bool(report.failed or report.errors)
    has_input_issue = any(
        ("not found" in w)
        or ("invalid XML" in w)
        or ("cannot read" in w)
        or w == "no input files"
        or w.endswith("skipped")
        or "declared tests=" in w
        for w in report.warnings
    )
    if args.strict and (has_problem or has_input_issue):
        return FAIL_EXIT
    return 0


if __name__ == "__main__":
    sys.exit(main())
