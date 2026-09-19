"""Regression test for the stdout/stderr pipe deadlock in runner.run().

Before the fix, runner.run() read stdout to completion in the foreground and
only drained stderr afterward via proc.communicate(). A subprocess that
writes more to stderr than fits in one OS pipe buffer (commonly 64KB) while
playcheck is still reading its stdout will block on that stderr write() —
while playcheck blocks on the stdout read() — and the two processes
deadlock forever.

ansible-playbook routinely writes more than that to stderr in real runs
(deprecation warnings, connection-plugin diagnostics, etc.), so this isn't
a contrived edge case.

This test stands in a fake "ansible-playbook" that writes a large amount of
stderr *before* any stdout, and asserts that run() still returns promptly
instead of hanging. It's run on a background thread with an explicit join
timeout so a regression fails the test instead of hanging the suite.
"""
from __future__ import annotations

import stat
import sys
import textwrap
import threading
from pathlib import Path

import pytest

from playcheck.runner import run

# Comfortably larger than any common OS pipe buffer (typically 64KB on Linux
# and macOS), so that writing this much before stdout closes reproduces the
# deadlock if stderr isn't drained concurrently.
STDERR_BYTES = 2 * 1024 * 1024

# Generous but finite: on the old (broken) code this test would otherwise
# hang forever, so we bound how long we wait for the background thread.
JOIN_TIMEOUT_SECONDS = 15


@pytest.fixture()
def fake_ansible_playbook(tmp_path: Path) -> Path:
    script = tmp_path / "fake-ansible-playbook"
    script.write_text(
        textwrap.dedent(
            f"""\
            #!{sys.executable}
            import sys

            # Write a large amount to stderr *before* touching stdout. A
            # caller that reads stdout to completion before draining stderr
            # will block on this write() once its stderr pipe buffer fills.
            sys.stderr.write("x" * {STDERR_BYTES})
            sys.stderr.flush()

            print('{{"event": "play_start", "play": "demo"}}')
            print('{{"event": "stats", "stats": {{}}}}')
            sys.stdout.flush()
            """
        )
    )
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    return script


def test_run_does_not_deadlock_on_large_stderr(fake_ansible_playbook):
    result: dict = {}

    def target() -> None:
        result["outcome"] = run(
            playbook="site.yml",
            inventories=["inv.ini"],
            extra_args=[],
            ansible_playbook_bin=str(fake_ansible_playbook),
            progress=False,
        )

    thread = threading.Thread(target=target, daemon=True)
    thread.start()
    thread.join(timeout=JOIN_TIMEOUT_SECONDS)

    assert not thread.is_alive(), (
        "runner.run() did not return within "
        f"{JOIN_TIMEOUT_SECONDS}s — it deadlocked on large stderr output"
    )

    outcome = result["outcome"]
    assert outcome.returncode == 0
    assert len(outcome.stderr) == STDERR_BYTES
    assert any("play_start" in line for line in outcome.stdout_lines)
    assert any("stats" in line for line in outcome.stdout_lines)


def test_run_reports_nonzero_exit_code(tmp_path: Path):
    script = tmp_path / "failing-ansible-playbook"
    script.write_text(f"#!{sys.executable}\nimport sys\nsys.exit(2)\n")
    script.chmod(script.stat().st_mode | stat.S_IEXEC)

    outcome = run(
        playbook="site.yml",
        inventories=["inv.ini"],
        extra_args=[],
        ansible_playbook_bin=str(script),
        progress=False,
    )
    assert outcome.returncode == 2
    assert outcome.stdout_lines == []
