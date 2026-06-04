#!/usr/bin/env python3
"""Security penetration testing audit script for web-agent.

Usage:
    uv run python tests/security/audit_script.py [--base-url http://localhost:8000]

Tests attack scenarios against the web-agent API to verify security controls.
Outputs a PASS/FAIL report suitable for CI security gates.
"""

from __future__ import annotations

import argparse
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass, field


@dataclass
class AuditResult:
    name: str
    passed: bool
    detail: str = ""
    severity: str = "INFO"


@dataclass
class AuditReport:
    results: list[AuditResult] = field(default_factory=list)

    def add(self, name: str, passed: bool, detail: str = "", severity: str = "INFO") -> None:
        self.results.append(AuditResult(name, passed, detail, severity))

    def summary(self) -> str:
        total = len(self.results)
        passed = sum(1 for r in self.results if r.passed)
        failed = total - passed
        critical_fails = [r for r in self.results if not r.passed and r.severity == "HIGH"]
        lines = [
            "=" * 60,
            "  SECURITY AUDIT REPORT",
            "=" * 60,
            f"  Total: {total}  Passed: {passed}  Failed: {failed}",
        ]
        if critical_fails:
            lines.append(f"  CRITICAL FAILURES: {len(critical_fails)}")
        lines.append("=" * 60)
        for r in self.results:
            status = "PASS" if r.passed else "FAIL"
            lines.append(f"  [{status}] [{r.severity}] {r.name}")
            if r.detail and not r.passed:
                lines.append(f"         {r.detail}")
        return "\n".join(lines)

    @property
    def all_passed(self) -> bool:
        return all(r.passed for r in self.results)


def run_audit(base_url: str) -> AuditReport:
    """Run all security audit checks against the target."""
    report = AuditReport()

    def req(method: str, path: str, headers: dict = None, body: bytes = None) -> tuple[int, str]:
        """Make an HTTP request and return (status_code, response_body)."""
        url = f"{base_url}{path}"
        hdrs = headers or {}
        try:
            r = urllib.request.Request(url, data=body, headers=hdrs, method=method)
            resp = urllib.request.urlopen(r, timeout=10)
            return resp.status, resp.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode("utf-8", errors="replace")
        except Exception as e:
            return 0, str(e)

    # 1. Authentication / Authorization
    status, body = req("GET", "/health")
    report.add("Health endpoint accessible", status == 200, f"Got {status}", "LOW")

    status, body = req("POST", "/api/users/alice/sessions")
    report.add(
        "Session creation requires authentication",
        status in (401, 403),
        f"Got {status} (expected 401 or 403)",
        "HIGH",
    )

    status, body = req(
        "POST", "/api/users/alice/sessions",
        headers={"Cookie": "access_token=invalid.fake.token"},
    )
    report.add(
        "Invalid token rejected",
        status in (401, 403),
        f"Got {status} (expected 401 or 403)",
        "HIGH",
    )

    # 2. CSRF Protection
    status, body = req(
        "POST", "/api/users/alice/sessions",
        headers={"Cookie": "access_token=valid; csrf_token=test-csrf"},
    )
    report.add(
        "CSRF: POST without X-CSRF-Token header rejected",
        status == 403,
        f"Got {status} (expected 403)",
        "HIGH",
    )

    # 3. Rate Limiting
    rate_limited = False
    for _ in range(40):
        try:
            status, _ = req("POST", "/api/users/alice/sessions")
            if status == 429:
                rate_limited = True
                break
        except Exception:
            pass
    report.add(
        "Rate limiting: rapid requests trigger 429",
        rate_limited,
        "Never got 429 after 40 rapid requests",
        "MEDIUM",
    )

    # 4. File Upload Security
    status, body = req(
        "POST", "/api/users/alice/upload",
        headers={"Content-Type": "multipart/form-data"},
        body=b"fake binary content",
    )
    report.add(
        "File upload: rejected without valid multipart",
        status != 200,
        f"Got {status}",
        "MEDIUM",
    )

    # 5. Information Disclosure
    status, body = req("GET", "/api/nonexistent/endpoint")
    report.add(
        "Info leak: 404 does not expose stack trace",
        "Traceback" not in body and "File \"" not in body,
        "Response may contain stack trace",
        "MEDIUM",
    )

    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Web Agent Security Audit")
    parser.add_argument(
        "--base-url", default="http://localhost:8000",
        help="Base URL of the web-agent server (default: http://localhost:8000)",
    )
    args = parser.parse_args()

    print(f"Running security audit against {args.base_url}")
    print()

    report = run_audit(args.base_url)
    print(report.summary())

    if not report.all_passed:
        print("\nSome security checks failed. Review the report above.")
        sys.exit(1)
    else:
        print("\nAll security checks passed.")
        sys.exit(0)


if __name__ == "__main__":
    main()
