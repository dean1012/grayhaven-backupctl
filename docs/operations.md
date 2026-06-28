# Operations

[Return to README.md](../README.md)

This guide covers operator backup and restore workflows using
`grayhaven-backupctl`.

## Table of Contents

- [Overview](#overview)
- [Listing Backups](#listing-backups)
- [Creating Backups](#creating-backups)
- [Listing Backup Contents](#listing-backup-contents)
- [Finding Backups Containing Paths](#finding-backups-containing-paths)
- [Restoring to a Target Directory](#restoring-to-a-target-directory)
- [Restoring In Place](#restoring-in-place)
- [Time Filters](#time-filters)
- [Path Files](#path-files)
- [Shell Completion](#shell-completion)
- [Retention Notes](#retention-notes)

## Overview

Run `grayhaven-backupctl` with `sudo` from the managed server whose backups
need to be reviewed or restored. By default, commands use both local and remote
repositories when remote backups are configured.

Use `--repo` to limit a command:

```bash
sudo grayhaven-backupctl list --repo local
sudo grayhaven-backupctl list --repo remote
sudo grayhaven-backupctl list --repo all
```

When both local and remote repositories can satisfy a restore request,
`grayhaven-backupctl` prefers the local repository unless `--repo remote` is
specified.

Relative paths are internalized as absolute paths before backup selection so
restores behave consistently regardless of the operator's current directory.

[Back to top](#operations)

## Listing Backups

List all available backups:

```bash
sudo grayhaven-backupctl list
```

List local backups only:

```bash
sudo grayhaven-backupctl list --repo local
```

List backups from the last two days:

```bash
sudo grayhaven-backupctl list --since "2 days ago"
```

The output shows the repository, shortest unique snapshot ID, timestamp, host,
and which directories are included in each snapshot.

[Back to top](#operations)

## Creating Backups

Create local and remote backups:

```bash
sudo grayhaven-backupctl backup
```

Create only a local backup:

```bash
sudo grayhaven-backupctl backup --repo local
```

Create only a remote backup:

```bash
sudo grayhaven-backupctl backup --repo remote
```

If remote backups are not configured, `backup --repo all` creates the local
backup and warns that no remote backup was created. `backup --repo remote`
warns that remote backups are not configured without creating a backup.

[Back to top](#operations)

## Listing Backup Contents

List a path in the latest backup:

```bash
sudo grayhaven-backupctl ls latest --path /home/example
```

List a path recursively in the latest backup:

```bash
sudo grayhaven-backupctl ls latest --path /home/example --recursive
```

List a path in a specific snapshot:

```bash
sudo grayhaven-backupctl ls abc12345 --path /home/example --recursive
```

By default, `ls` is non-recursive. Add `--recursive` when reviewing a directory
tree.

[Back to top](#operations)

## Finding Backups Containing Paths

Find all backups that contain a file or directory:

```bash
sudo grayhaven-backupctl find --path /home/example/report.txt
```

Find all backups with paths matching a glob:

```bash
sudo grayhaven-backupctl find --path '/home/example/*.txt'
```

Find all backups in a time window that contain a file or directory:

```bash
sudo grayhaven-backupctl find --path /home/example/report.txt --since "7 days ago"
```

Find inspects snapshot contents recursively so files beneath a requested
directory can be found.

[Back to top](#operations)

## Restoring to a Target Directory

Restore a file from the latest matching backup into the current directory:

```bash
sudo grayhaven-backupctl restore --path /home/example/report.txt
```

Restore a file from the latest matching backup into a target directory:

```bash
sudo grayhaven-backupctl restore --target /tmp/grayhaven-restore --path /tmp/report.txt
```

The archived absolute path tree is preserved under the target. For example,
restoring `/tmp/report.txt` to `/tmp/grayhaven-restore` creates:

```text
/tmp/grayhaven-restore/tmp/report.txt
```

Restore from a specific snapshot into the current directory:

```bash
sudo grayhaven-backupctl restore abc12345 --path /home/example/report.txt
```

If the destination already exists, the command warns before overwriting.
`--force` can be used to bypass these warnings:

```bash
sudo grayhaven-backupctl restore --force --path /home/example/report.txt
```

[Back to top](#operations)

## Restoring In Place

Restore directly to the original path:

```bash
sudo grayhaven-backupctl restore --in-place --path /home/example/report.txt
```

Restore multiple paths in place:

```bash
sudo grayhaven-backupctl restore \
  --in-place \
  --path /home/example/report.txt \
  --path /home/example/project
```

Each requested path is restored from the newest matching backup that contains
that path. If one path exists only in an older backup, only that path falls back
to the older snapshot.

In-place restore warns before overwriting existing files or directories unless
`--force` is specified.

[Back to top](#operations)

## Time Filters

Use `--since` and `--until` to filter backup listings, searches, and restore
selection:

```bash
sudo grayhaven-backupctl list --since "2 days ago"
sudo grayhaven-backupctl find \
  --path /home/example/report.txt \
  --until "2026-06-27 8:00 AM"
```

Use `--as-of` with restore to choose the closest matching backup at or before a
time:

```bash
sudo grayhaven-backupctl restore --as-of "3 days ago" --path /home/example/report.txt
```

Supported inputs include natural-language expressions such as `2 days ago` and
explicit timestamps such as `2026-06-27 8:00 AM`.

[Back to top](#operations)

## Path Files

For larger restore or search operations, place one path per line in a path file:

```text
/home/example/report.txt
/home/example/project
/root/operator-notes
```

Blank lines and lines beginning with `#` are ignored.

Use the path file with `find`:

```bash
sudo grayhaven-backupctl find --path-file /tmp/grayhaven-paths.txt
```

Use the path file with restore:

```bash
sudo grayhaven-backupctl restore --path-file /tmp/grayhaven-paths.txt
```

[Back to top](#operations)

## Shell Completion

Print the bash completion script:

```bash
grayhaven-backupctl completion bash
```

Load completion in the current shell:

```bash
source <(grayhaven-backupctl completion bash)
```

System-wide completion installation is handled by
[grayhaven-config-ansible](https://github.com/dean1012/grayhaven-config-ansible).

[Back to top](#operations)

## Retention Notes

`grayhaven-backupctl` is subject to configured backup retention rules. Running
multiple manual backups within the configured retention period may result in
older backups being pruned.

[Back to top](#operations)
