"""Invoke ansible-playbook --check --diff with the playcheck callback plugin."""
from __future__ import annotations

import os
import subprocess
import sys
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

PLUGIN_DIR = Path(__file__).parent / "_ansible" / "callback_plugins"


@dataclass
class RunOutcome:
    returncode: int
    stdout_lines: List[str] = field(default_factory=list)
    stderr: str = ""


class AnsibleNotFound(Exception):
    pass


def build_command(
    playbook: str,
    inventories: List[str],
    extra_args: List[str],
    ansible_playbook_bin: str = "ansible-playbook",
) -> List[str]:
    cmd = [ansible_playbook_bin, playbook, "--check", "--diff"]
    for inv in inventories:
        cmd += ["-i", inv]
    cmd += extra_args
    return cmd


def build_env(base_env: Optional[dict] = None) -> dict:
    env = dict(base_env if base_env is not None else os.environ)
    env["ANSIBLE_STDOUT_CALLBACK"] = "playcheck_jsonl"
    plugin_path = str(PLUGIN_DIR)
    existing = env.get("ANSIBLE_CALLBACK_PLUGINS")
    env["ANSIBLE_CALLBACK_PLUGINS"] = (
        plugin_path + os.pathsep + existing if existing else plugin_path
    )
    # Our callback owns stdout; ansible color codes would corrupt the JSONL.
    env.pop("ANSIBLE_FORCE_COLOR", None)
    env["ANSIBLE_NOCOLOR"] = "1"
    return env


def run(
    playbook: str,
    inventories: List[str],
    extra_args: List[str],
    ansible_playbook_bin: str = "ansible-playbook",
    progress: bool = True,
) -> RunOutcome:
    cmd = build_command(playbook, inventories, extra_args, ansible_playbook_bin)
    try:
        proc = subprocess.Popen(
            cmd,
            env=build_env(),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except FileNotFoundError:
        raise AnsibleNotFound(
            f"could not find '{ansible_playbook_bin}' on PATH. "
            "Is Ansible installed in this environment?"
        )

    # ansible-playbook can write a surprising amount to stderr (deprecation
    # warnings, connection-plugin chatter, vault prompts on misconfiguration)
    # — easily enough to fill the OS pipe buffer (commonly 64KB). If we only
    # read stdout in this thread and leave stderr to `communicate()`
    # afterward, a full stderr pipe blocks the child on write() while we
    # block on read() of stdout: a classic subprocess deadlock. Drain stderr
    # concurrently on a background thread instead.
    stderr_chunks: List[str] = []

    def _drain_stderr() -> None:
        assert proc.stderr is not None
        for chunk in proc.stderr:
            stderr_chunks.append(chunk)

    stderr_thread = threading.Thread(target=_drain_stderr, daemon=True)
    stderr_thread.start()

    lines: List[str] = []
    try:
        assert proc.stdout is not None
        for line in proc.stdout:
            lines.append(line)
            if progress:
                _echo_progress(line)
    except KeyboardInterrupt:
        # Ctrl-C during the child's run: make sure we don't leave an
        # orphaned ansible-playbook process, and don't hang forever
        # waiting for it to exit on its own.
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
        raise
    finally:
        if proc.stdout is not None:
            proc.stdout.close()
        if proc.poll() is None:
            returncode = proc.wait()
        else:
            returncode = proc.returncode
        stderr_thread.join()

    return RunOutcome(
        returncode=returncode, stdout_lines=lines, stderr="".join(stderr_chunks)
    )


def _echo_progress(line: str) -> None:
    """Show a lightweight live trace on stderr while ansible runs."""
    import json

    try:
        event = json.loads(line)
    except ValueError:
        return
    kind = event.get("event")
    if kind == "play_start":
        sys.stderr.write(f"PLAY {event.get('play', '')}\n")
    elif kind == "task_start":
        sys.stderr.write(f"  · {event.get('task', '')}\n")
    sys.stderr.flush()
