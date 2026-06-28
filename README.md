# Grayhaven Systems LLC Backup (grayhaven-backupctl)

[![CI](https://github.com/dean1012/grayhaven-backupctl/actions/workflows/ci.yml/badge.svg)](https://github.com/dean1012/grayhaven-backupctl/actions/workflows/ci.yml)
[![Unit Tests](https://github.com/dean1012/grayhaven-backupctl/actions/workflows/unit-tests.yml/badge.svg)](https://github.com/dean1012/grayhaven-backupctl/actions/workflows/unit-tests.yml)
[![codecov](https://codecov.io/gh/dean1012/grayhaven-backupctl/graph/badge.svg)](https://codecov.io/gh/dean1012/grayhaven-backupctl)

Operator-friendly backup and restore utility for Grayhaven Systems LLC managed
servers.

## Table of Contents

- [Scope](#scope)
- [Managed Environment](#managed-environment)
- [Quick Usage](#quick-usage)
- [Documentation](#documentation)
- [Contributing](#contributing)
- [License](#license)

## Scope

`grayhaven-backupctl` wraps the restic repositories configured by
[grayhaven-config-ansible](https://github.com/dean1012/grayhaven-config-ansible)
for Grayhaven Systems LLC managed servers. It gives operators one command for
common backup review, search, and restore tasks while leaving restic as the
authoritative backup engine.

The utility supports:

- local and optional remote restic repositories;
- local-first restore selection when both repositories contain the requested
  path;
- natural-language and explicit timestamp filters;
- file, directory, glob, and path-file restore selection;
- in-place restore with overwrite confirmation;
- target-directory restore that preserves the archived absolute path tree;
- SELinux context restoration after files are restored;
- journald logging for backup and restore actions.

[Back to top](#grayhaven-systems-llc-backup-grayhaven-backupctl)

## Managed Environment

`grayhaven-backupctl` is installed and maintained by
[grayhaven-config-ansible](https://github.com/dean1012/grayhaven-config-ansible)
on Grayhaven Systems LLC managed servers. It expects the host layout, restic
configuration, backup runner, system packages, and optional remote backup
credentials managed by that repository.

This repository is not a general-purpose backup utility template. Using similar
automation outside the Grayhaven Systems LLC managed environment requires
review and adaptation.

[Back to top](#grayhaven-systems-llc-backup-grayhaven-backupctl)

## Quick Usage

List local and remote backups:

```bash
sudo grayhaven-backupctl list
```

List recent backups:

```bash
sudo grayhaven-backupctl list --since "2 days ago"
```

Create local and remote backups, when remote backups are configured:

```bash
sudo grayhaven-backupctl backup
```

Find local or remote backups that contain a path:

```bash
sudo grayhaven-backupctl find --path /home/example/report.txt
```

List the contents of a directory from the latest backup recursively:

```bash
sudo grayhaven-backupctl ls latest --path /home/example --recursive
```

Restore a file to a target directory:

```bash
sudo grayhaven-backupctl restore --target /tmp/grayhaven-restore --path /tmp/report.txt
```

Restore a file to its original location:

```bash
sudo grayhaven-backupctl restore --in-place --path /home/example/report.txt
```

[Back to top](#grayhaven-systems-llc-backup-grayhaven-backupctl)

## Documentation

Operator backup and restore procedures are documented in
[Operations](docs/operations.md).

[Back to top](#grayhaven-systems-llc-backup-grayhaven-backupctl)

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for development setup, validation
commands, and contribution guidelines.

[Back to top](#grayhaven-systems-llc-backup-grayhaven-backupctl)

## License

[MIT](LICENSE)

[Back to top](#grayhaven-systems-llc-backup-grayhaven-backupctl)
