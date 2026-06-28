#!/usr/bin/env python3
"""Unit tests for grayhaven-backupctl."""

from __future__ import annotations

import contextlib
import io
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from importlib.machinery import SourceFileLoader
from types import ModuleType
from typing import Any, ClassVar
from unittest import mock

# ---------------------------------------------------------------------------
# Module loading
# ---------------------------------------------------------------------------

MODULE_PATH = pathlib.Path(__file__).resolve().parents[1] / "grayhaven-backupctl"
backupctl_module = ModuleType("grayhaven_backupctl")
sys.modules[backupctl_module.__name__] = backupctl_module
SourceFileLoader(backupctl_module.__name__, str(MODULE_PATH)).exec_module(
    backupctl_module
)


# ---------------------------------------------------------------------------
# Test fixtures
# ---------------------------------------------------------------------------


class FakeRunner(backupctl_module.CommandRunner):
    """Command runner fixture with deterministic command responses."""

    def __init__(self, responses: dict[tuple[str, ...], str] | None = None) -> None:
        self.responses = responses or {}
        self.calls: list[list[str]] = []

    def run(self, argv, *, env=None, check=True, capture=True):
        self.calls.append(list(argv))
        output = self.responses.get(tuple(argv), "")
        return mock.Mock(stdout=output, stderr="", returncode=0)


class OSErrorRunner(backupctl_module.CommandRunner):
    """Runner fixture that simulates an unavailable command."""

    def run(self, argv, *, env=None, check=True, capture=True):
        raise OSError("logger unavailable")


# ---------------------------------------------------------------------------
# Unit tests
# ---------------------------------------------------------------------------


class GrayhavenBackupctlTests(unittest.TestCase):
    """Coverage for grayhaven-backupctl commands and core backup behavior."""

    def setUp(self) -> None:
        self.config = backupctl_module.BackupConfig(
            local_repository="/var/backups/restic",
            remote_enabled=True,
            remote_repository="gs:host-restic:/",
            password_file="/etc/grayhaven/backup/restic-password",
            include_file="/etc/grayhaven/backup/include",
            exclude_file="/etc/grayhaven/backup/exclude",
            keep_daily=7,
            restic_cache_dir="/var/cache/restic",
            gcs_credentials_file="/etc/grayhaven/backup/gcs-credentials.json",
            gcs_project_id="grayhaven",
        )

    def test_reads_authoritative_backup_script_config(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = pathlib.Path(temp_dir) / "grayhaven-restic-backup"
            config_path.write_text(
                "\n".join(
                    [
                        'LOCAL_REPOSITORY="/var/backups/restic"',
                        "REMOTE_ENABLED=true",
                        'REMOTE_REPOSITORY="gs:test-restic:/"',
                        'PASSWORD_FILE="/secret/password"',
                        'INCLUDE_FILE="/backup/include"',
                        'EXCLUDE_FILE="/backup/exclude"',
                        "KEEP_DAILY=7",
                        'RESTIC_CACHE_DIR="/var/cache/restic"',
                        'GCS_CREDENTIALS_FILE="/secret/gcs.json"',
                        "GCS_PROJECT_ID=grayhaven",
                    ]
                ),
                encoding="utf-8",
            )

            config = backupctl_module.BackupConfig.from_backup_script(config_path)

        self.assertEqual(config.local_repository, "/var/backups/restic")
        self.assertTrue(config.remote_enabled)
        self.assertEqual(config.remote_repository, "gs:test-restic:/")
        self.assertEqual(config.gcs_project_id, "grayhaven")

    def test_backup_config_defaults_remote_disabled_when_not_declared(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = pathlib.Path(temp_dir) / "grayhaven-restic-backup"
            config_path.write_text(
                "\n".join(
                    [
                        'LOCAL_REPOSITORY="/var/backups/restic"',
                        'PASSWORD_FILE="/secret/password"',
                        'INCLUDE_FILE="/backup/include"',
                        'EXCLUDE_FILE="/backup/exclude"',
                        "KEEP_DAILY=7",
                        'RESTIC_CACHE_DIR="/var/cache/restic"',
                    ]
                ),
                encoding="utf-8",
            )

            config = backupctl_module.BackupConfig.from_backup_script(config_path)

        self.assertFalse(config.remote_enabled)
        self.assertIsNone(config.remote_repository)
        self.assertIsNone(config.gcs_credentials_file)

    def test_backup_config_reports_unreadable_script(self) -> None:
        with self.assertRaisesRegex(
            backupctl_module.GrayhavenBackupctlError,
            "Unable to read backup configuration",
        ):
            backupctl_module.BackupConfig.from_backup_script(
                pathlib.Path("/missing/grayhaven-restic-backup")
            )

    def test_backup_config_reports_missing_required_values(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = pathlib.Path(temp_dir) / "grayhaven-restic-backup"
            config_path.write_text(
                "\n".join(
                    [
                        "# comments and shell logic are ignored",
                        "REMOTE_ENABLED=false",
                        'LOCAL_REPOSITORY="/var/backups/restic"',
                    ]
                ),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(
                backupctl_module.GrayhavenBackupctlError, "PASSWORD_FILE"
            ):
                backupctl_module.BackupConfig.from_backup_script(config_path)

    def test_repositories_default_to_local_and_remote(self) -> None:
        service = backupctl_module.BackupService(self.config, FakeRunner())

        repos = service.repositories("all")

        self.assertEqual([repo.name for repo in repos], ["local", "remote"])
        self.assertEqual(repos[1].env["GOOGLE_PROJECT_ID"], "grayhaven")
        self.assertEqual(
            repos[1].env["GOOGLE_APPLICATION_CREDENTIALS"],
            "/etc/grayhaven/backup/gcs-credentials.json",
        )
        self.assertTrue(service.remote_configured())

    def test_remote_repository_is_absent_when_not_configured(self) -> None:
        config = dataclass_replace(
            self.config, remote_enabled=False, remote_repository=None
        )
        service = backupctl_module.BackupService(config, FakeRunner())

        self.assertEqual([repo.name for repo in service.repositories("all")], ["local"])
        self.assertEqual(service.repositories("remote"), [])
        self.assertFalse(service.remote_configured())

    def test_remote_repository_env_omits_absent_gcs_values(self) -> None:
        config = dataclass_replace(
            self.config, gcs_project_id=None, gcs_credentials_file=None
        )
        repo = backupctl_module.Repository("remote", "gs:host-restic:/", config)

        env = repo.env

        self.assertNotIn("GOOGLE_PROJECT_ID", env)
        self.assertNotIn("GOOGLE_APPLICATION_CREDENTIALS", env)
        self.assertEqual(env["RESTIC_CACHE_DIR"], "/var/cache/restic")

    def test_command_runner_executes_commands(self) -> None:
        runner = backupctl_module.CommandRunner()

        result = runner.run([sys.executable, "-c", "print('runner-ok')"])

        self.assertEqual(result.stdout.strip(), "runner-ok")

    def test_journal_logger_swallows_logger_failures(self) -> None:
        logger = backupctl_module.JournalLogger(OSErrorRunner())

        logger.log('action="test"')

    def test_parse_explicit_iso_timestamp(self) -> None:
        parsed = backupctl_module.parse_human_time("2026-06-27T00:45:00-05:00")

        self.assertIsNotNone(parsed)
        self.assertEqual(parsed.year, 2026)
        self.assertEqual(parsed.minute, 45)

    def test_parse_explicit_human_timestamp(self) -> None:
        parsed = backupctl_module.parse_human_time("2026-06-27 12:45 AM")

        self.assertIsNotNone(parsed)
        self.assertEqual(parsed.year, 2026)
        self.assertEqual(parsed.hour, 0)
        self.assertEqual(parsed.minute, 45)

    def test_parse_natural_past_timestamp(self) -> None:
        parsed = backupctl_module.parse_human_time("5 minutes ago")

        self.assertIsNotNone(parsed)
        self.assertLess(parsed, datetime.now().astimezone())
        self.assertGreater(parsed, datetime.now().astimezone() - timedelta(minutes=10))

    def test_parse_natural_future_timestamp_for_until(self) -> None:
        parsed = backupctl_module.parse_human_time("5 minutes from now")

        self.assertIsNotNone(parsed)
        self.assertGreater(parsed, datetime.now().astimezone())
        self.assertLess(parsed, datetime.now().astimezone() + timedelta(minutes=10))

    def test_parse_human_time_rejects_unknown_expression(self) -> None:
        with self.assertRaisesRegex(
            backupctl_module.GrayhavenBackupctlError, "Could not parse"
        ):
            backupctl_module.parse_human_time("nonsense blurple time")

    def test_parse_time_helpers_cover_optional_and_explicit_inputs(self) -> None:
        self.assertIsNone(backupctl_module.parse_human_time(None))
        self.assertIsNone(backupctl_module.parse_explicit_time("   "))
        self.assertIsNone(backupctl_module.parse_explicit_time("not a timestamp"))

        zulu = backupctl_module.parse_explicit_time("2026-06-27T01:02:03Z")
        date_only = backupctl_module.parse_explicit_time("2026-06-27")
        twenty_four_hour = backupctl_module.parse_explicit_time("2026-06-27 08:30")

        self.assertIsNotNone(zulu)
        self.assertIsNotNone(date_only)
        self.assertIsNotNone(twenty_four_hour)
        self.assertEqual(zulu.year, 2026)
        self.assertEqual(date_only.hour, 0)
        self.assertEqual(twenty_four_hour.minute, 30)

    def test_parse_human_time_accepts_naive_natural_language_result(self) -> None:
        class FakeContext:
            hasDateOrTime = True

        calendar = mock.Mock()
        calendar.parseDT.return_value = (datetime(2026, 6, 27, 8, 30), FakeContext())

        with mock.patch.object(
            backupctl_module.parsedatetime, "Calendar", return_value=calendar
        ):
            parsed = backupctl_module.parse_human_time("fake fuzzy time")

        self.assertIsNotNone(parsed)
        self.assertIsNotNone(parsed.tzinfo)

    def test_parse_restic_time_assumes_utc_for_naive_timestamp(self) -> None:
        parsed = backupctl_module.parse_restic_time("2026-06-27T01:02:03")

        self.assertIsNotNone(parsed.tzinfo)
        self.assertEqual(parsed.year, 2026)

    def test_format_timestamp_uses_operator_friendly_format(self) -> None:
        formatted = backupctl_module.format_timestamp(
            datetime.fromisoformat("2026-06-27T13:45:00+00:00")
        )

        self.assertIn("2026-06-27", formatted)
        self.assertRegex(formatted, r"0?1:45:00 PM|[0-9]{2}:45:00 [AP]M")

    def test_lists_snapshots_with_unique_short_ids_and_time_filter(self) -> None:
        old_id = "aaaaaaaa11111111222222223333333344444444"
        new_id = "aaaaaaaa99999999222222223333333344444444"
        snapshots = [
            {
                "id": old_id,
                "time": "2026-06-20T01:00:00Z",
                "hostname": "host-a",
                "paths": ["/home"],
            },
            {
                "id": new_id,
                "time": "2026-06-22T01:00:00Z",
                "hostname": "host-a",
                "paths": ["/home"],
            },
        ]
        runner = FakeRunner(
            {
                restic_key("/var/backups/restic", "snapshots", "--json"): json.dumps(
                    snapshots
                ),
                restic_key("gs:host-restic:/", "snapshots", "--json"): "[]",
            }
        )
        service = backupctl_module.BackupService(self.config, runner)

        result = service.snapshots(
            "all", since=datetime.fromisoformat("2026-06-21T00:00:00+00:00")
        )

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].id, new_id)
        self.assertGreater(
            len(result[0].short_id), backupctl_module.MIN_SNAPSHOT_PREFIX
        )

    def test_lists_snapshots_applies_until_filter(self) -> None:
        old_id = "abababab11111111222222223333333344444444"
        new_id = "babababa11111111222222223333333344444444"
        runner = FakeRunner(
            {
                restic_key("/var/backups/restic", "snapshots", "--json"): json.dumps(
                    [
                        snapshot_record(old_id, "2026-06-20T01:00:00Z"),
                        snapshot_record(new_id, "2026-06-22T01:00:00Z"),
                    ]
                )
            }
        )
        service = backupctl_module.BackupService(self.config, runner)

        result = service.snapshots(
            "local", until=datetime.fromisoformat("2026-06-21T00:00:00+00:00")
        )

        self.assertEqual([snapshot.id for snapshot in result], [old_id])

    def test_run_restic_reports_stderr_and_generic_failures(self) -> None:
        repo = backupctl_module.Repository("local", "/var/backups/restic", self.config)
        runner = mock.Mock(spec=backupctl_module.CommandRunner)
        runner.run.side_effect = subprocess.CalledProcessError(
            1, ["restic"], stderr="bad password"
        )
        service = backupctl_module.BackupService(self.config, runner)

        with self.assertRaisesRegex(
            backupctl_module.GrayhavenBackupctlError, "bad password"
        ):
            service.run_restic(repo, ["snapshots"])

        runner.run.side_effect = subprocess.CalledProcessError(1, ["restic"], stderr="")
        with self.assertRaisesRegex(
            backupctl_module.GrayhavenBackupctlError, "restic command failed for local"
        ):
            service.run_restic(repo, ["snapshots"])

    def test_find_supports_globs(self) -> None:
        snapshot_id = "bbbbbbbb11111111222222223333333344444444"
        runner = FakeRunner(
            {
                restic_key("/var/backups/restic", "snapshots", "--json"): json.dumps(
                    [
                        {
                            "id": snapshot_id,
                            "time": "2026-06-22T01:00:00Z",
                            "hostname": "host-a",
                            "paths": ["/home"],
                        }
                    ]
                ),
                restic_key("gs:host-restic:/", "snapshots", "--json"): "[]",
                restic_key(
                    "/var/backups/restic",
                    "ls",
                    snapshot_id,
                    "--json",
                    "--recursive",
                    "/home/jsmith",
                ): "\n".join(
                    [
                        json.dumps({"type": "dir", "path": "/home/jsmith"}),
                        json.dumps({"type": "file", "path": "/home/jsmith/report.txt"}),
                    ]
                ),
            }
        )
        service = backupctl_module.BackupService(self.config, runner)

        matches = service.find_matches("local", ["/home/jsmith/*.txt"])

        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0].matched_path, "/home/jsmith/report.txt")

    def test_ls_ignores_blank_snapshot_and_malformed_lines(self) -> None:
        snapshot_id = "1212121211111111222222223333333344444444"
        runner = FakeRunner(
            {
                restic_key(
                    "/var/backups/restic", "ls", snapshot_id, "--json", "/home/jsmith"
                ): "\n".join(
                    [
                        "",
                        "{not-json}",
                        json.dumps({"type": "snapshot", "path": "/"}),
                        json.dumps({"type": "file", "path": "/home/jsmith/report.txt"}),
                    ]
                )
            }
        )
        service = backupctl_module.BackupService(self.config, runner)

        paths = service.list_snapshot_contents(
            backupctl_module.Repository("local", "/var/backups/restic", self.config),
            snapshot_id,
            "/home/jsmith",
        )

        self.assertEqual(paths, ["/home/jsmith/report.txt"])

    def test_ls_is_non_recursive_by_default(self) -> None:
        snapshot_id = "abababab11111111222222223333333344444444"
        runner = FakeRunner(
            {
                restic_key(
                    "/var/backups/restic", "ls", snapshot_id, "--json", "/home/jsmith"
                ): json.dumps({"type": "dir", "path": "/home/jsmith"})
            }
        )
        service = backupctl_module.BackupService(self.config, runner)

        paths = service.list_snapshot_contents(
            backupctl_module.Repository("local", "/var/backups/restic", self.config),
            snapshot_id,
            "/home/jsmith",
        )

        self.assertEqual(paths, ["/home/jsmith"])
        self.assertIn(
            list(
                restic_key(
                    "/var/backups/restic", "ls", snapshot_id, "--json", "/home/jsmith"
                )
            ),
            runner.calls,
        )

    def test_ls_supports_recursive_listing(self) -> None:
        snapshot_id = "cdcdcdcd11111111222222223333333344444444"
        runner = FakeRunner(
            {
                restic_key(
                    "/var/backups/restic",
                    "ls",
                    snapshot_id,
                    "--json",
                    "--recursive",
                    "/home/jsmith",
                ): "\n".join(
                    [
                        json.dumps({"type": "dir", "path": "/home/jsmith"}),
                        json.dumps({"type": "file", "path": "/home/jsmith/report.txt"}),
                    ]
                )
            }
        )
        service = backupctl_module.BackupService(self.config, runner)

        paths = service.list_snapshot_contents(
            backupctl_module.Repository("local", "/var/backups/restic", self.config),
            snapshot_id,
            "/home/jsmith",
            recursive=True,
        )

        self.assertEqual(paths, ["/home/jsmith", "/home/jsmith/report.txt"])
        self.assertIn(
            list(
                restic_key(
                    "/var/backups/restic",
                    "ls",
                    snapshot_id,
                    "--json",
                    "--recursive",
                    "/home/jsmith",
                )
            ),
            runner.calls,
        )

    def test_restore_latest_resolves_each_path_independently(self) -> None:
        newer_id = "cccccccc11111111222222223333333344444444"
        older_id = "dddddddd11111111222222223333333344444444"
        runner = FakeRunner(
            {
                restic_key("/var/backups/restic", "snapshots", "--json"): json.dumps(
                    [
                        snapshot_record(newer_id, "2026-06-22T01:00:00Z"),
                        snapshot_record(older_id, "2026-06-21T01:00:00Z"),
                    ]
                ),
                restic_key("gs:host-restic:/", "snapshots", "--json"): "[]",
                restic_key(
                    "/var/backups/restic",
                    "ls",
                    newer_id,
                    "--json",
                    "--recursive",
                    "/home/jsmith/new.txt",
                ): json.dumps({"type": "file", "path": "/home/jsmith/new.txt"}),
                restic_key(
                    "/var/backups/restic",
                    "ls",
                    newer_id,
                    "--json",
                    "--recursive",
                    "/home/jsmith/old.txt",
                ): "",
                restic_key(
                    "/var/backups/restic",
                    "ls",
                    older_id,
                    "--json",
                    "--recursive",
                    "/home/jsmith/new.txt",
                ): "",
                restic_key(
                    "/var/backups/restic",
                    "ls",
                    older_id,
                    "--json",
                    "--recursive",
                    "/home/jsmith/old.txt",
                ): json.dumps({"type": "file", "path": "/home/jsmith/old.txt"}),
            }
        )
        service = backupctl_module.BackupService(self.config, runner)

        plan = service.resolve_restore_plan(
            "local",
            "latest",
            ["/home/jsmith/new.txt", "/home/jsmith/old.txt"],
            pathlib.Path("/tmp/restore"),
            in_place=False,
        )

        self.assertEqual([item.snapshot.id for item in plan], [newer_id, older_id])
        self.assertEqual(
            plan[0].destination_path, pathlib.Path("/tmp/restore/home/jsmith/new.txt")
        )

    def test_restore_in_place_targets_original_path(self) -> None:
        snapshot_id = "3434343411111111222222223333333344444444"
        runner = FakeRunner(
            {
                restic_key("/var/backups/restic", "snapshots", "--json"): json.dumps(
                    [snapshot_record(snapshot_id, "2026-06-22T01:00:00Z")]
                ),
                restic_key(
                    "/var/backups/restic",
                    "ls",
                    snapshot_id,
                    "--json",
                    "--recursive",
                    "/home/jsmith/file.txt",
                ): json.dumps({"type": "file", "path": "/home/jsmith/file.txt"}),
            }
        )
        service = backupctl_module.BackupService(self.config, runner)

        plan = service.resolve_restore_plan(
            "local",
            snapshot_id[:8],
            ["/home/jsmith/file.txt"],
            pathlib.Path("/tmp/restore"),
            in_place=True,
        )

        self.assertEqual(
            plan[0].destination_path, pathlib.Path("/home/jsmith/file.txt")
        )

    def test_restore_plan_reports_missing_path(self) -> None:
        service = backupctl_module.BackupService(self.config, FakeRunner())

        with self.assertRaisesRegex(
            backupctl_module.GrayhavenBackupctlError, "No local snapshot contains"
        ):
            service.resolve_restore_plan(
                "local",
                "latest",
                ["/home/jsmith/missing.txt"],
                pathlib.Path("/tmp/restore"),
                in_place=False,
            )

    def test_restore_latest_prefers_local_when_all_repos_match(self) -> None:
        local_id = "eeeeeeee11111111222222223333333344444444"
        remote_id = "ffffffff11111111222222223333333344444444"
        runner = FakeRunner(
            {
                restic_key("/var/backups/restic", "snapshots", "--json"): json.dumps(
                    [snapshot_record(local_id, "2026-06-22T01:00:00Z")]
                ),
                restic_key("gs:host-restic:/", "snapshots", "--json"): json.dumps(
                    [snapshot_record(remote_id, "2026-06-22T01:05:00Z")]
                ),
                restic_key(
                    "/var/backups/restic",
                    "ls",
                    local_id,
                    "--json",
                    "--recursive",
                    "/home/jsmith/file.txt",
                ): json.dumps({"type": "file", "path": "/home/jsmith/file.txt"}),
                restic_key(
                    "gs:host-restic:/",
                    "ls",
                    remote_id,
                    "--json",
                    "--recursive",
                    "/home/jsmith/file.txt",
                ): json.dumps({"type": "file", "path": "/home/jsmith/file.txt"}),
            }
        )
        service = backupctl_module.BackupService(self.config, runner)

        plan = service.resolve_restore_plan(
            "all",
            "latest",
            ["/home/jsmith/file.txt"],
            pathlib.Path("/tmp/restore"),
            in_place=False,
        )

        self.assertEqual(plan[0].repo.name, "local")
        self.assertEqual(plan[0].snapshot.id, local_id)

    def test_restore_time_window_excludes_newer_snapshot(self) -> None:
        newer_id = "1111111111111111222222223333333344444444"
        older_id = "2222222211111111222222223333333344444444"
        runner = FakeRunner(
            {
                restic_key("/var/backups/restic", "snapshots", "--json"): json.dumps(
                    [
                        snapshot_record(newer_id, "2026-06-23T01:00:00Z"),
                        snapshot_record(older_id, "2026-06-21T01:00:00Z"),
                    ]
                ),
                restic_key("gs:host-restic:/", "snapshots", "--json"): "[]",
                restic_key(
                    "/var/backups/restic",
                    "ls",
                    older_id,
                    "--json",
                    "--recursive",
                    "/home/jsmith/file.txt",
                ): json.dumps({"type": "file", "path": "/home/jsmith/file.txt"}),
            }
        )
        service = backupctl_module.BackupService(self.config, runner)

        plan = service.resolve_restore_plan(
            "local",
            "latest",
            ["/home/jsmith/file.txt"],
            pathlib.Path("/tmp/restore"),
            in_place=False,
            until=datetime.fromisoformat("2026-06-22T00:00:00+00:00"),
        )

        self.assertEqual(plan[0].snapshot.id, older_id)

    def test_restore_executes_restic_and_copies_result(self) -> None:
        repo = backupctl_module.Repository("local", "/var/backups/restic", self.config)
        snapshot = backupctl_module.Snapshot(
            repo="local",
            short_id="abababab",
            id="abababab11111111222222223333333344444444",
            time=datetime.fromisoformat("2026-06-22T01:00:00+00:00"),
            host="host-a",
            paths=("/home",),
        )
        plan = [
            backupctl_module.RestoreItem(
                repo=repo,
                snapshot=snapshot,
                requested_path="/home/jsmith/file.txt",
                source_path="/home/jsmith/file.txt",
                destination_path=pathlib.Path("/restore/home/jsmith/file.txt"),
            )
        ]
        service = backupctl_module.BackupService(self.config, FakeRunner())

        def fake_run_restic(_repo, args, *, check=True):
            target = pathlib.Path(args[args.index("--target") + 1])
            restored = target / "home" / "jsmith" / "file.txt"
            restored.parent.mkdir(parents=True)
            restored.write_text("restored", encoding="utf-8")
            return ""

        with mock.patch.object(service, "run_restic", side_effect=fake_run_restic):
            with mock.patch.object(backupctl_module, "copy_restored_path") as copy_path:
                with mock.patch.object(service.journal, "log") as journal_log:
                    with contextlib.redirect_stdout(io.StringIO()) as stdout:
                        service.restore(plan, force=True, verbose=True)

        self.assertIn("Repository: /var/backups/restic", stdout.getvalue())
        copy_path.assert_called_once()
        journal_log.assert_called_once()

    def test_restore_reports_missing_restored_source(self) -> None:
        repo = backupctl_module.Repository("local", "/var/backups/restic", self.config)
        snapshot = backupctl_module.Snapshot(
            repo="local",
            short_id="abababab",
            id="abababab11111111222222223333333344444444",
            time=datetime.fromisoformat("2026-06-22T01:00:00+00:00"),
            host="host-a",
            paths=("/home",),
        )
        plan = [
            backupctl_module.RestoreItem(
                repo=repo,
                snapshot=snapshot,
                requested_path="/home/jsmith/file.txt",
                source_path="/home/jsmith/file.txt",
                destination_path=pathlib.Path("/restore/home/jsmith/file.txt"),
            )
        ]
        service = backupctl_module.BackupService(self.config, FakeRunner())

        with mock.patch.object(service, "run_restic", return_value=""):
            with contextlib.redirect_stdout(io.StringIO()):
                with self.assertRaisesRegex(
                    backupctl_module.GrayhavenBackupctlError, "did not restore"
                ):
                    service.restore(plan, force=True, verbose=False)

    def test_restore_confirms_overwrite_when_needed(self) -> None:
        repo = backupctl_module.Repository("local", "/var/backups/restic", self.config)
        snapshot = backupctl_module.Snapshot(
            repo="local",
            short_id="abababab",
            id="abababab11111111222222223333333344444444",
            time=datetime.fromisoformat("2026-06-22T01:00:00+00:00"),
            host="host-a",
            paths=("/home",),
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            destination = pathlib.Path(temp_dir) / "file.txt"
            destination.write_text("old", encoding="utf-8")
            plan = [
                backupctl_module.RestoreItem(
                    repo=repo,
                    snapshot=snapshot,
                    requested_path="/home/jsmith/file.txt",
                    source_path="/home/jsmith/file.txt",
                    destination_path=destination,
                )
            ]
            service = backupctl_module.BackupService(self.config, FakeRunner())

            def fake_run_restic(_repo, args, *, check=True):
                target = pathlib.Path(args[args.index("--target") + 1])
                restored = target / "home" / "jsmith" / "file.txt"
                restored.parent.mkdir(parents=True)
                restored.write_text("restored", encoding="utf-8")
                return ""

            with mock.patch.object(service, "run_restic", side_effect=fake_run_restic):
                with mock.patch.object(
                    backupctl_module, "copy_restored_path"
                ) as copy_path:
                    with mock.patch("builtins.input", return_value="RESTORE"):
                        with contextlib.redirect_stdout(io.StringIO()):
                            with contextlib.redirect_stderr(io.StringIO()):
                                service.restore(plan, force=False, verbose=False)

            self.assertTrue(copy_path.call_args.kwargs["overwrite"])

    def test_copy_restored_path_replaces_existing_directory_after_confirmation(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = pathlib.Path(temp_dir)
            source = root / "source"
            destination = root / "destination"
            source.mkdir()
            destination.mkdir()
            (source / "new.txt").write_text("new", encoding="utf-8")
            (destination / "old.txt").write_text("old", encoding="utf-8")

            with mock.patch.object(backupctl_module.os, "chown"):
                with mock.patch.object(
                    backupctl_module.selinux, "is_selinux_enabled", return_value=0
                ):
                    backupctl_module.copy_restored_path(
                        source, destination, overwrite=True
                    )

            self.assertTrue((destination / "new.txt").exists())
            self.assertFalse((destination / "old.txt").exists())

    def test_copy_restored_path_preserves_file_ownership(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = pathlib.Path(temp_dir)
            source = root / "source.txt"
            destination = root / "destination.txt"
            source.write_text("restored", encoding="utf-8")

            with mock.patch.object(backupctl_module.os, "chown") as chown:
                with mock.patch.object(
                    backupctl_module.selinux, "is_selinux_enabled", return_value=0
                ):
                    backupctl_module.copy_restored_path(
                        source, destination, overwrite=True
                    )

            source_stat = source.lstat()
            chown.assert_called_once_with(
                destination,
                source_stat.st_uid,
                source_stat.st_gid,
                follow_symlinks=False,
            )

    def test_copy_restored_path_overwrites_existing_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = pathlib.Path(temp_dir)
            source = root / "source.txt"
            destination = root / "destination.txt"
            source.write_text("new", encoding="utf-8")
            destination.write_text("old", encoding="utf-8")

            with mock.patch.object(backupctl_module.os, "chown"):
                with mock.patch.object(
                    backupctl_module.selinux, "is_selinux_enabled", return_value=0
                ):
                    backupctl_module.copy_restored_path(
                        source, destination, overwrite=True
                    )

            self.assertEqual(destination.read_text(encoding="utf-8"), "new")

    def test_copy_restored_path_preserves_directory_ownership_recursively(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = pathlib.Path(temp_dir)
            source = root / "source"
            destination = root / "destination"
            nested_file = source / "nested" / "file.txt"
            nested_file.parent.mkdir(parents=True)
            nested_file.write_text("restored", encoding="utf-8")

            with mock.patch.object(backupctl_module.os, "chown") as chown:
                with mock.patch.object(
                    backupctl_module.selinux, "is_selinux_enabled", return_value=0
                ):
                    backupctl_module.copy_restored_path(
                        source, destination, overwrite=True
                    )

            expected_paths = {
                destination,
                destination / "nested",
                destination / "nested" / "file.txt",
            }
            self.assertEqual(
                {call.args[0] for call in chown.call_args_list}, expected_paths
            )

    def test_copy_restored_path_preserves_restored_ancestor_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = pathlib.Path(temp_dir)
            source_root = root / "restored"
            source_home = source_root / "home"
            source_user = source_home / "jsmith"
            source_file = source_user / "myfile"
            destination = root / "target" / "home" / "jsmith" / "myfile"
            source_user.mkdir(parents=True)
            source_file.write_text("restored", encoding="utf-8")
            source_home.chmod(0o755)
            source_user.chmod(0o700)

            with mock.patch.object(backupctl_module.os, "chown"):
                with mock.patch.object(
                    backupctl_module.selinux, "is_selinux_enabled", return_value=0
                ):
                    backupctl_module.copy_restored_path(
                        source_file,
                        destination,
                        overwrite=True,
                        source_root=source_root,
                    )

            self.assertEqual(
                oct((root / "target" / "home").stat().st_mode & 0o777), "0o755"
            )
            self.assertEqual(
                oct((root / "target" / "home" / "jsmith").stat().st_mode & 0o777),
                "0o700",
            )

    def test_copy_restored_path_restores_selinux_context_when_enabled(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = pathlib.Path(temp_dir)
            source = root / "source.txt"
            destination = root / "destination.txt"
            source.write_text("restored", encoding="utf-8")

            with (
                mock.patch.object(backupctl_module.os, "chown"),
                mock.patch.object(
                    backupctl_module.selinux, "is_selinux_enabled", return_value=1
                ),
                mock.patch.object(
                    backupctl_module.selinux,
                    "matchpathcon",
                    return_value=(0, "context"),
                    create=True,
                ),
                mock.patch.object(
                    backupctl_module.selinux,
                    "SELINUX_RESTORECON_RECURSE",
                    1,
                    create=True,
                ),
                mock.patch.object(
                    backupctl_module.selinux,
                    "SELINUX_RESTORECON_IGNORE_NOENTRY",
                    2,
                    create=True,
                ),
                mock.patch.object(
                    backupctl_module.selinux, "selinux_restorecon", create=True
                ) as restorecon,
            ):
                backupctl_module.copy_restored_path(source, destination, overwrite=True)

            restorecon.assert_called_once_with(os.path.abspath(destination), 3)

    def test_copy_restored_path_skips_selinux_context_without_policy_entry(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = pathlib.Path(temp_dir)
            source = root / "source.txt"
            destination = root / "destination.txt"
            source.write_text("restored", encoding="utf-8")

            with (
                mock.patch.object(backupctl_module.os, "chown"),
                mock.patch.object(
                    backupctl_module.selinux, "is_selinux_enabled", return_value=1
                ),
                mock.patch.object(
                    backupctl_module.selinux,
                    "matchpathcon",
                    side_effect=FileNotFoundError,
                    create=True,
                ),
                mock.patch.object(
                    backupctl_module.selinux, "selinux_restorecon", create=True
                ) as restorecon,
            ):
                backupctl_module.copy_restored_path(source, destination, overwrite=True)

            restorecon.assert_not_called()

    def test_confirm_overwrite_rejects_unconfirmed_restore(self) -> None:
        with mock.patch("builtins.input", return_value="nope"):
            with contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaisesRegex(
                    backupctl_module.GrayhavenBackupctlError, "cancelled"
                ):
                    backupctl_module.confirm_overwrite(pathlib.Path("/restore/file"))

    def test_backup_all_uses_authoritative_script(self) -> None:
        runner = FakeRunner({(str(backupctl_module.BACKUP_SCRIPT),): ""})
        service = backupctl_module.BackupService(self.config, runner)

        with contextlib.redirect_stdout(io.StringIO()) as stdout:
            service.backup("all", verbose=False)

        self.assertIn([str(backupctl_module.BACKUP_SCRIPT)], runner.calls)
        self.assertIn("Backup completed", stdout.getvalue())

    def test_backup_all_warns_when_remote_is_not_configured(self) -> None:
        config = dataclass_replace(
            self.config, remote_enabled=False, remote_repository=None
        )
        runner = FakeRunner({(str(backupctl_module.BACKUP_SCRIPT),): ""})
        service = backupctl_module.BackupService(config, runner)

        with contextlib.redirect_stdout(io.StringIO()) as stdout:
            service.backup("all", verbose=False)

        self.assertIn("remote backups are not configured", stdout.getvalue())
        self.assertIn([str(backupctl_module.BACKUP_SCRIPT)], runner.calls)

    def test_backup_remote_warns_when_remote_is_not_configured(self) -> None:
        config = dataclass_replace(
            self.config, remote_enabled=False, remote_repository=None
        )
        runner = FakeRunner()
        service = backupctl_module.BackupService(config, runner)

        with contextlib.redirect_stdout(io.StringIO()) as stdout:
            service.backup("remote", verbose=False)

        self.assertIn("remote backups are not configured", stdout.getvalue())
        self.assertFalse(any(call and call[0] == "restic" for call in runner.calls))

    def test_backup_local_runs_restic_backup_and_retention(self) -> None:
        runner = FakeRunner()
        service = backupctl_module.BackupService(self.config, runner)

        with mock.patch.object(
            backupctl_module, "hostname_fqdn", return_value="host.example.com"
        ):
            with contextlib.redirect_stdout(io.StringIO()) as stdout:
                service.backup("local", verbose=True)

        self.assertIn("Creating local backup", stdout.getvalue())
        self.assertIn("Repository: /var/backups/restic", stdout.getvalue())
        self.assertIn(
            list(
                restic_key(
                    "/var/backups/restic",
                    "backup",
                    "--files-from",
                    "/etc/grayhaven/backup/include",
                    "--exclude-file",
                    "/etc/grayhaven/backup/exclude",
                    "--tag",
                    "grayhaven-local",
                    "--host",
                    "host.example.com",
                )
            ),
            runner.calls,
        )
        self.assertIn(
            list(
                restic_key(
                    "/var/backups/restic", "forget", "--keep-daily", "7", "--prune"
                )
            ),
            runner.calls,
        )

    def test_authoritative_backup_reports_failures_and_verbose_output(self) -> None:
        runner = mock.Mock(spec=backupctl_module.CommandRunner)
        runner.run.return_value = mock.Mock(stdout="backup output\n")
        service = backupctl_module.BackupService(self.config, runner)

        with contextlib.redirect_stdout(io.StringIO()) as stdout:
            service.run_authoritative_backup(verbose=True)

        self.assertIn("backup output", stdout.getvalue())

        runner.run.side_effect = subprocess.CalledProcessError(
            1, ["backup"], stderr="backup failed"
        )
        with self.assertRaisesRegex(
            backupctl_module.GrayhavenBackupctlError, "backup failed"
        ):
            service.run_authoritative_backup(verbose=False)

        runner.run.side_effect = subprocess.CalledProcessError(1, ["backup"], stderr="")
        with self.assertRaisesRegex(
            backupctl_module.GrayhavenBackupctlError, "Backup script failed"
        ):
            service.run_authoritative_backup(verbose=False)

    def test_hostname_fqdn_uses_system_hostname(self) -> None:
        with mock.patch.object(
            backupctl_module.subprocess,
            "run",
            return_value=mock.Mock(stdout="host.example.com\n"),
        ):
            self.assertEqual(backupctl_module.hostname_fqdn(), "host.example.com")

    def test_path_helpers_handle_files_globs_and_path_files(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path_file = pathlib.Path(temp_dir) / "paths.txt"
            path_file.write_text(
                "\n".join(
                    [
                        "# comment",
                        "",
                        "./alpha.txt",
                        "~/beta.txt",
                    ]
                ),
                encoding="utf-8",
            )
            args = mock.Mock(path=["../gamma.txt"], path_file=str(path_file))

            result = backupctl_module.read_requested_paths(args)

        self.assertEqual(len(result), 3)
        self.assertTrue(all(path.startswith("/") for path in result))
        self.assertEqual(backupctl_module.probe_root_for_pattern("*.log"), "/")
        self.assertEqual(
            backupctl_module.probe_root_for_pattern("/home/*/app.log"), "/home"
        )
        self.assertEqual(
            backupctl_module.probe_root_for_pattern("/home/jsmith/app*.log"),
            "/home/jsmith",
        )
        self.assertEqual(
            backupctl_module.probe_root_for_pattern("[abc].log"),
            "/",
        )
        self.assertEqual(
            backupctl_module.first_matching_path(
                "/home/jsmith/project/",
                ["/home/jsmith/project"],
            ),
            "/home/jsmith/project",
        )
        self.assertEqual(
            backupctl_module.first_matching_path(
                "/home/jsmith/*.txt", ["/home/jsmith/a.log"]
            ),
            None,
        )
        self.assertEqual(backupctl_module.first_matching_path("/", ["/"]), "/")
        self.assertTrue(backupctl_module.has_glob("/home/*"))

    def test_read_requested_paths_reports_file_and_empty_errors(self) -> None:
        missing_file_args = mock.Mock(path=[], path_file="/missing/path-list")
        with self.assertRaisesRegex(
            backupctl_module.GrayhavenBackupctlError, "Unable to read path file"
        ):
            backupctl_module.read_requested_paths(missing_file_args)

        empty_args = mock.Mock(path=[], path_file=None)
        with self.assertRaisesRegex(
            backupctl_module.GrayhavenBackupctlError, "At least one"
        ):
            backupctl_module.read_requested_paths(empty_args)

    def test_snapshot_helpers_match_prefixes_and_priorities(self) -> None:
        snapshot = backupctl_module.Snapshot(
            repo="local",
            short_id="abc12345",
            id="abc123456789",
            time=datetime.now().astimezone(),
            host="host-a",
            paths=("/home",),
        )

        self.assertTrue(backupctl_module.snapshot_matches("abc12345", snapshot))
        self.assertTrue(backupctl_module.snapshot_matches("abc123456", snapshot))
        self.assertFalse(backupctl_module.snapshot_matches("def12345", snapshot))
        self.assertEqual(backupctl_module.repo_restore_priority("all", "remote"), 1)
        self.assertEqual(backupctl_module.repo_restore_priority("local", "local"), 0)
        self.assertEqual(
            backupctl_module.relative_tree("/home/example"),
            pathlib.Path("home/example"),
        )

        prefixes = backupctl_module.unique_prefix_lengths(
            [
                "aaaaaaaa11111111222222223333333344444444",
                "bbbbbbbb11111111222222223333333344444444",
            ]
        )
        self.assertEqual(prefixes["aaaaaaaa11111111222222223333333344444444"], 8)

    def test_print_helpers_render_empty_and_populated_results(self) -> None:
        snapshot = backupctl_module.Snapshot(
            repo="local",
            short_id="abc12345",
            id="abc123456789",
            time=datetime.now().astimezone(),
            host="host-a",
            paths=("/home",),
        )
        repo = backupctl_module.Repository("local", "/repo", self.config)
        match = backupctl_module.PathMatch(
            repo, snapshot, "/home/example", "/home/example"
        )

        with contextlib.redirect_stdout(io.StringIO()) as stdout:
            backupctl_module.print_snapshots([])
            backupctl_module.print_matches([])
            backupctl_module.print_paths([])
            backupctl_module.print_snapshots([snapshot])
            backupctl_module.print_matches([match])
            backupctl_module.print_paths(["/home/example"])

        output = stdout.getvalue()
        self.assertIn("No backups found", output)
        self.assertIn("No matching backups found", output)
        self.assertIn("No paths found", output)
        self.assertIn("SNAPSHOT", output)
        self.assertIn("/home/example", output)

    def test_main_prints_help_without_command(self) -> None:
        with contextlib.redirect_stdout(io.StringIO()) as stdout:
            status = backupctl_module.main([])

        self.assertEqual(status, 0)
        self.assertIn(
            "Manage Grayhaven local and remote restic backups", stdout.getvalue()
        )

    def test_main_prints_bash_completion_without_backup_config(self) -> None:
        with mock.patch.object(
            backupctl_module.BackupConfig, "from_backup_script"
        ) as config_loader:
            with contextlib.redirect_stdout(io.StringIO()) as stdout:
                status = backupctl_module.main(["completion", "bash"])

        self.assertEqual(status, 0)
        self.assertIn(
            "complete -F _grayhaven_backupctl grayhaven-backupctl", stdout.getvalue()
        )
        config_loader.assert_not_called()

    def test_bash_completion_includes_operator_options(self) -> None:
        completion = backupctl_module.bash_completion_script()

        self.assertIn("--repo", completion)
        self.assertIn("--as-of", completion)
        self.assertIn("--path-file", completion)
        self.assertIn("--recursive", completion)
        self.assertIn("_filedir", completion)
        self.assertIn("all local remote", completion)

    def test_main_dispatches_every_command(self) -> None:
        with mock.patch.object(
            backupctl_module.BackupConfig,
            "from_backup_script",
            return_value=self.config,
        ):
            with mock.patch.object(backupctl_module, "BackupService", FakeService):
                commands = [
                    ["list"],
                    ["ls", "latest", "--path", "/home/example"],
                    ["ls", "latest", "--path", "/home/example", "--recursive"],
                    ["find", "--path", "/home/example"],
                    ["restore", "--path", "/home/example", "--force"],
                    ["backup"],
                ]
                for argv in commands:
                    with self.subTest(argv=argv):
                        with contextlib.redirect_stdout(io.StringIO()):
                            self.assertEqual(backupctl_module.main(argv), 0)

    def test_main_routes_command_arguments_to_service(self) -> None:
        cases = [
            (
                ["list", "--repo", "local", "--since", "2026-06-27"],
                ("snapshots", "local", True, False),
            ),
            (
                [
                    "find",
                    "--repo",
                    "remote",
                    "--path",
                    "/home/example",
                    "--until",
                    "2026-06-27 8:00 AM",
                ],
                ("find_matches", "remote", ("/home/example",), False, True),
            ),
            (
                [
                    "restore",
                    "--repo",
                    "local",
                    "--path",
                    "/home/example",
                    "--target",
                    "/tmp/restore",
                    "--force",
                ],
                ("restore", True, False),
            ),
            (
                ["--verbose", "backup", "--repo", "local"],
                ("backup", "local", True),
            ),
        ]

        for argv, expected_call in cases:
            with self.subTest(argv=argv):
                RecordingService.instances = []
                with mock.patch.object(
                    backupctl_module.BackupConfig,
                    "from_backup_script",
                    return_value=self.config,
                ):
                    with mock.patch.object(
                        backupctl_module, "BackupService", RecordingService
                    ):
                        with contextlib.redirect_stdout(io.StringIO()):
                            self.assertEqual(backupctl_module.main(argv), 0)

                self.assertIn(expected_call, RecordingService.instances[0].calls)

    def test_main_routes_ls_arguments_to_service(self) -> None:
        RecordingService.instances = []
        with mock.patch.object(
            backupctl_module.BackupConfig,
            "from_backup_script",
            return_value=self.config,
        ):
            with mock.patch.object(backupctl_module, "BackupService", RecordingService):
                with contextlib.redirect_stdout(io.StringIO()):
                    status = backupctl_module.main(
                        [
                            "ls",
                            "abc12345",
                            "--repo",
                            "remote",
                            "--path",
                            "/home/example",
                            "--recursive",
                        ]
                    )

        service = RecordingService.instances[0]
        self.assertEqual(status, 0)
        self.assertIn(
            ("find_matches", "remote", ("/home/example",), False, False),
            service.calls,
        )
        self.assertIn(
            ("list_snapshot_contents", "remote", "abc123456789", "/home/example", True),
            service.calls,
        )

    def test_main_rejects_argparse_errors(self) -> None:
        cases = [
            ["list", "--repo", "bogus"],
            ["ls", "latest"],
            ["restore", "--path", "/home/example", "--target", "/tmp", "--in-place"],
            ["completion", "zsh"],
        ]

        for argv in cases:
            with self.subTest(argv=argv):
                with contextlib.redirect_stderr(io.StringIO()):
                    with self.assertRaises(SystemExit) as exc:
                        backupctl_module.main(argv)

                self.assertEqual(exc.exception.code, 2)

    def test_main_reports_user_facing_errors(self) -> None:
        with (
            mock.patch.object(
                backupctl_module.BackupConfig,
                "from_backup_script",
                side_effect=backupctl_module.GrayhavenBackupctlError("config broken"),
            ),
            contextlib.redirect_stderr(io.StringIO()) as stderr,
        ):
            status = backupctl_module.main(["list"])

        self.assertEqual(status, 1)
        self.assertIn("config broken", stderr.getvalue())

    def test_main_ls_reports_missing_snapshot(self) -> None:
        with mock.patch.object(
            backupctl_module.BackupConfig,
            "from_backup_script",
            return_value=self.config,
        ):
            with mock.patch.object(backupctl_module, "BackupService", EmptyService):
                with contextlib.redirect_stderr(io.StringIO()) as stderr:
                    status = backupctl_module.main(["ls", "--path", "/home/example"])

        self.assertEqual(status, 1)
        self.assertIn("No all snapshot contains", stderr.getvalue())

    def test_restore_rejects_as_of_with_until(self) -> None:
        with mock.patch.object(
            backupctl_module.BackupConfig,
            "from_backup_script",
            return_value=self.config,
        ):
            with contextlib.redirect_stderr(io.StringIO()) as stderr:
                status = backupctl_module.main(
                    [
                        "restore",
                        "--path",
                        "/home/example",
                        "--as-of",
                        "3 days ago",
                        "--until",
                        "2 days ago",
                    ]
                )

        self.assertEqual(status, 1)
        self.assertIn("--as-of and --until cannot be used together", stderr.getvalue())

    def test_restore_rejects_as_of_with_explicit_snapshot(self) -> None:
        with mock.patch.object(
            backupctl_module.BackupConfig,
            "from_backup_script",
            return_value=self.config,
        ):
            with contextlib.redirect_stderr(io.StringIO()) as stderr:
                status = backupctl_module.main(
                    [
                        "restore",
                        "abc12345",
                        "--path",
                        "/home/example",
                        "--as-of",
                        "3 days ago",
                    ]
                )

        self.assertEqual(status, 1)
        self.assertIn("--as-of can only be used with latest", stderr.getvalue())


# ---------------------------------------------------------------------------
# CLI service fixtures
# ---------------------------------------------------------------------------


class FakeService:
    """Minimal service used to verify CLI command dispatch."""

    def __init__(self, config):
        self.config = config

    def snapshots(self, selector, since=None, until=None):
        return []

    def find_matches(self, selector, requested_paths, since=None, until=None):
        repo = backupctl_module.Repository("local", "/repo", self.config)
        snapshot = backupctl_module.Snapshot(
            repo="local",
            short_id="abc12345",
            id="abc123456789",
            time=datetime.now().astimezone(),
            host="host-a",
            paths=("/home",),
        )
        return [
            backupctl_module.PathMatch(
                repo=repo,
                snapshot=snapshot,
                requested_path=requested_paths[0],
                matched_path=requested_paths[0],
            )
        ]

    def list_snapshot_contents(self, repo, snapshot_id, path, *, recursive=False):
        return []

    def resolve_restore_plan(
        self,
        selector,
        snapshot_ref,
        requested_paths,
        target_dir,
        in_place,
        since=None,
        until=None,
    ):
        return []

    def restore(self, plan, force, verbose):
        return None

    def backup(self, selector, verbose):
        return None


class EmptyService(FakeService):
    """Service fixture that returns no path matches."""

    def find_matches(self, selector, requested_paths, since=None, until=None):
        return []


class RecordingService(FakeService):
    """Service fixture that records CLI argument routing."""

    instances: ClassVar[list[RecordingService]] = []

    def __init__(self, config):
        super().__init__(config)
        self.calls = []
        self.snapshot = backupctl_module.Snapshot(
            repo="remote",
            short_id="abc12345",
            id="abc123456789",
            time=datetime.now().astimezone(),
            host="host-a",
            paths=("/home",),
        )
        self.repo = backupctl_module.Repository("remote", "/repo", config)
        self.__class__.instances.append(self)

    def snapshots(self, selector, since=None, until=None):
        self.calls.append(("snapshots", selector, since is not None, until is not None))
        return []

    def find_matches(self, selector, requested_paths, since=None, until=None):
        self.calls.append(
            (
                "find_matches",
                selector,
                tuple(requested_paths),
                since is not None,
                until is not None,
            )
        )
        return [
            backupctl_module.PathMatch(
                repo=self.repo,
                snapshot=self.snapshot,
                requested_path=requested_paths[0],
                matched_path=requested_paths[0],
            )
        ]

    def list_snapshot_contents(self, repo, snapshot_id, path, *, recursive=False):
        self.calls.append(
            ("list_snapshot_contents", repo.name, snapshot_id, path, recursive)
        )
        return [path]

    def resolve_restore_plan(
        self,
        selector,
        snapshot_ref,
        requested_paths,
        target_dir,
        in_place,
        since=None,
        until=None,
    ):
        self.calls.append(
            (
                "resolve_restore_plan",
                selector,
                snapshot_ref,
                tuple(requested_paths),
                pathlib.Path(target_dir),
                in_place,
                since is not None,
                until is not None,
            )
        )
        return []

    def restore(self, plan, force, verbose):
        self.calls.append(("restore", force, verbose))

    def backup(self, selector, verbose):
        self.calls.append(("backup", selector, verbose))


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------


def restic_key(repo: str, *args: str) -> tuple[str, ...]:
    return (
        "restic",
        "--repo",
        repo,
        "--password-file",
        "/etc/grayhaven/backup/restic-password",
        *args,
    )


def snapshot_record(snapshot_id: str, timestamp: str) -> dict[str, object]:
    return {
        "id": snapshot_id,
        "time": timestamp,
        "hostname": "host-a",
        "paths": ["/home"],
    }


def dataclass_replace(config: Any, **changes: Any) -> Any:
    return backupctl_module.dataclasses.replace(config, **changes)


# ---------------------------------------------------------------------------
# Test entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    unittest.main()
