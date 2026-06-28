# Contributing

Thank you for your interest in improving `grayhaven-backupctl`.

## Table of Contents

- [Development Setup](#development-setup)
- [Validation](#validation)
- [Pull Requests](#pull-requests)
- [Documentation Guidelines](#documentation-guidelines)

## Development Setup

Install the operating system packages needed by the utility and test suite.
`python3-libselinux` is required because restore operations relabel restored
paths through the system SELinux bindings:

```bash
sudo dnf install git python3 python3-libselinux python3-pip
```

Create and activate a Python 3.12 or newer virtual environment with access to
system site packages:

```bash
python3 -m venv --system-site-packages .venv
source .venv/bin/activate
```

Install runtime and development dependencies:

```bash
python3 -m pip install --upgrade pip
python3 -m pip install -r requirements.txt
python3 -m pip install -r requirements-dev.txt
```

[Back to top](#contributing)

## Validation

Run the same validation commands used by CI:

```bash
python3 -m pip_audit --progress-spinner off -r requirements.txt
python3 -m py_compile grayhaven-backupctl
python3 -m coverage run -m unittest discover -s tests -v
python3 -m coverage report
python3 -m coverage xml
mypy --strict grayhaven-backupctl
ruff check grayhaven-backupctl tests
ruff format --check grayhaven-backupctl tests
git ls-files '*.yml' '*.yaml' | xargs -r yamllint
git ls-files '*.md' | xargs -r markdownlint-cli2
```

The coverage report measures application code and fails if coverage falls below
the 90% threshold configured in `pyproject.toml`.

CI also generates `coverage.xml` and uploads it to Codecov using GitHub Actions
OIDC authentication. No `CODECOV_TOKEN` repository secret is required.
Project coverage checks and pull request comments are configured in
`codecov.yml`.

Before committing changes, also check the current diff for whitespace errors:

```bash
git diff --check
```

[Back to top](#contributing)

## Pull Requests

Create a focused feature branch for each change. Reference the related issue in
each commit and include `Closes #<issue-number>` in the pull request
description when the pull request should close an issue after merging.

Sign each commit so GitHub can verify its authorship. The `main` branch ruleset
requires signed commits before merging:

```bash
git commit -S -m "<message> (Refs #<issue-number>)"
```

CI runs on pushes, pull requests, and manual workflow dispatches. Dependabot
checks Python packages and GitHub Actions weekly.

[Back to top](#contributing)

## Documentation Guidelines

Keep user-facing behavior documented in `README.md` and
`docs/operations.md`. Keep contributor workflows documented in
`CONTRIBUTING.md`.

In Python code, use docstrings for module, class, and function
responsibilities. Add inline comments for non-obvious implementation
decisions, security boundaries, and assumptions. Avoid comments that merely
restate straightforward code.

[Back to top](#contributing)
