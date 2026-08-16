# playcheck     
     
[![CI](https://github.com/Cjayy77/Playcheck/actions/workflows/ci.yml/badge.svg)](https://github.com/Cjayy77/Playcheck/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/playcheck)](https://pypi.org/project/playcheck/)
[![Python](https://img.shields.io/pypi/pyversions/playcheck)](https://pypi.org/project/playcheck/)
[![ansible-core](https://img.shields.io/badge/ansible--core-2.15%20%E2%86%92%202.21-black)](https://github.com/Cjayy77/Playcheck/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)
 
Readable previews for `ansible-playbook --check --diff`.
  
Ansible's check mode tells you what a playbook *would* change — but the raw
output is an unstructured scroll, and tasks that can't be simulated   
(`shell`, `command`, `raw`, …) are silently skipped with the same
`skipping:` line as an ordinary conditional skip. People read "no diff shown"
as "no change", and that's false.

playcheck runs the check for you and reformats the result so it's readable in
ten seconds:

- **Per-host grouping** — `web-01: 4 changes · 2 not previewable`
- **Colored add/remove diffs** instead of raw unified-diff walls
- **Explicit flagging of every task that could not be previewed**, including
  tasks with `check_mode: false` that executed for real during the check
- **Top-line summary** — hosts affected, tasks that would change, tasks that
  could not be simulated

```
$ playcheck run site.yml -i inventory.ini

web-01  4 changes · 2 not previewable
  ~ Write nginx config (copy)
      +server {
      +  listen 80;
      +}
  ! Restart nginx (shell)  NOT PREVIEWED — Command would have run if not in check mode
  ~ Write API token (copy)  [diff censored (no_log)]

SUMMARY
  hosts: 2 of 2 would change
  tasks: 5 would change (2 with hidden diffs)
  ⚠ 3 tasks were NOT simulated (module does not support check mode) — the real run may change more than shown.
```

## Install

```
pipx install playcheck   # or: pip install playcheck
```

Requires Python ≥ 3.9 and an existing `ansible-playbook` on PATH
(ansible-core ≥ 2.9). No other dependencies.

## Usage

```
playcheck run <playbook> -i <inventory> [-l LIMIT] [-t TAGS] [--no-color] [--quiet]
```

Anything after `--` is passed to `ansible-playbook` unchanged:

```
playcheck run site.yml -i prod.ini -- -e env=prod --vault-password-file .vault
```

playcheck adds `--check --diff` itself — it never applies changes. The exit
code mirrors `ansible-playbook`'s (0 on success even when changes are
pending; non-zero on task failures or unreachable hosts).

For CI gating there are two opt-in exit codes, checked in this order after a
clean ansible run:

| flag | exit code | meaning |
|---|---|---|
| `--fail-on-changes` | 3 | at least one task would change something |
| `--fail-on-unpreviewable` | 4 | at least one task could not be simulated |

`--format markdown` emits a GitHub-flavored report (collapsible per-host
sections, fenced diffs) suitable for PR comments and `$GITHUB_STEP_SUMMARY`.

## GitHub Action

Post the preview as a PR comment, `terraform plan`-style. The comment is
updated in place on subsequent pushes instead of spamming the thread:

```yaml
on: pull_request

jobs:
  preview:
    runs-on: ubuntu-latest
    permissions:
      pull-requests: write
    steps:
      - uses: actions/checkout@v4
      - uses: Cjayy77/Playcheck@v0
        with:
          playbook: site.yml
          inventory: inventory/prod.ini
          ansible-core-version: "2.17"   # optional
          extra-args: "-l web -e env=prod"
```

## How it works

playcheck doesn't scrape ansible's human-readable output (which is ambiguous
— an unsupported-check-mode skip and a `when:`-conditional skip print
identically). Instead it ships a tiny stdout callback plugin and invokes
`ansible-playbook` with `ANSIBLE_STDOUT_CALLBACK=playcheck_jsonl`, receiving
one structured JSON event per task result, including diffs, skip reasons, and
check-mode metadata.

Safety notes:

- `no_log` results are censored by Ansible **before** they reach any callback;
  playcheck never sees the secret.
- `diff: false` tasks arrive with an empty diff; playcheck marks them
  `[diff hidden by task setting]` rather than pretending nothing changed.
- Any skip that can't be positively attributed to a `when:` condition is
  flagged as *not previewed*. Over-flagging is a feature: silently missing an
  unsimulated task is the exact failure this tool exists to prevent.

## Status

Alpha. CLI formatter and GitHub Action work; GitLab CI wrapper is planned.

Verified against **ansible-core 2.15, 2.17, 2.19, and 2.21** with real
`--check --diff` runs — the test suite replays against fresh captures from
each version (`scripts/version_matrix.sh`, also run in CI), not hand-written
fixtures. Classification never depends on exact skip-message text: any skip
that can't be positively attributed to a `when:` conditional is flagged, which
is what catches `raw` (skipped with no message at all) and whatever future
ansible versions do differently.

## Development

```
pip install -e . pytest
pytest

# regenerate fixtures from a real run (Linux/WSL, needs ansible-core):
ANSIBLE_STDOUT_CALLBACK=playcheck_jsonl \
ANSIBLE_CALLBACK_PLUGINS=src/playcheck/_ansible/callback_plugins \
ansible-playbook testdata/site.yml -i testdata/inventory.ini --check --diff \
  > tests/fixtures/jsonl_output.jsonl
```

## License

MIT
