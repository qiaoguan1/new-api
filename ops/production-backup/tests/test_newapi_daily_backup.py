import hashlib
import importlib.util
import os
import json
import sqlite3
import shutil
import tarfile
import tempfile
import unittest
import copy
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import patch


SCRIPT = Path(__file__).resolve().parents[1] / "newapi_daily_backup.py"
SPEC = importlib.util.spec_from_file_location("newapi_daily_backup", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


class FakeRunner:
    def __init__(self) -> None:
        self.calls = []

    def __call__(self, arguments, **kwargs):
        self.calls.append((arguments, kwargs))
        if hasattr(kwargs.get("stdout"), "write"):
            kwargs["stdout"].write(b"PGDMP fake custom archive")
        if kwargs.get("stdin") is not None:
            self.assert_dump(kwargs["stdin"].read())
        return None

    @staticmethod
    def assert_dump(content: bytes) -> None:
        if not content.startswith(b"PGDMP"):
            raise AssertionError("dump was not custom format")


class DailyBackupTests(unittest.TestCase):
    @staticmethod
    def create_stack(workspace: Path) -> Path:
        stack = workspace / "stack"
        (stack / "nginx" / "conf.d").mkdir(parents=True)
        (stack / ".env").write_text("SECRET=private\n", encoding="utf-8")
        (stack / "docker-compose.yml").write_text("services: {}\n", encoding="utf-8")
        (stack / "nginx" / "conf.d" / "default.conf").write_text("server {}\n", encoding="utf-8")
        return stack

    def test_external_sqlite_backup_includes_uncheckpointed_wal_and_private_sources(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            stack = self.create_stack(workspace)
            external = workspace / "gateway"
            external.mkdir()
            database = external / "jobs.sqlite3"
            connection = sqlite3.connect(database)
            try:
                connection.execute("PRAGMA journal_mode=WAL")
                connection.execute("PRAGMA wal_autocheckpoint=0")
                connection.execute("CREATE TABLE jobs (id TEXT PRIMARY KEY, status TEXT)")
                connection.execute("INSERT INTO jobs VALUES ('accepted-original', 'running')")
                connection.commit()
                self.assertTrue(Path(str(database) + "-wal").is_file())
                (external / "credentials.json").write_text('{"key":"private"}\n', encoding="utf-8")
                root = MODULE.OptionalExternalRecoveryRoot("image-gateway", external, required=True,
                    required_sqlite=("jobs.sqlite3",))
                result = MODULE.run_backup(stack_root=stack, backup_root=workspace / "backups",
                    retain=14, runner=FakeRunner(), external_roots=(root,), now=MODULE.datetime(2026, 10, 10, 3, 30))
                metadata = json.loads((result / "metadata.json").read_text(encoding="utf-8"))
                self.assertEqual(metadata["coverage_status"], "configured_complete")
                with tarfile.open(result / "recovery-config.tar.gz", "r:gz") as archive:
                    names = archive.getnames()
                    self.assertNotIn("external/image-gateway/jobs.sqlite3-wal", names)
                    self.assertNotIn("external/image-gateway/jobs.sqlite3-shm", names)
                    for member in archive.getmembers():
                        self.assertTrue(member.isfile())
                        self.assertEqual(member.mode, 0o600)
                        self.assertEqual(member.uid, 0)
                        self.assertEqual(member.gid, 0)
                    snapshot = workspace / "restored.sqlite3"
                    snapshot.write_bytes(archive.extractfile("external/image-gateway/jobs.sqlite3").read())
                restored = sqlite3.connect(snapshot)
                try:
                    self.assertEqual(restored.execute("SELECT id, status FROM jobs").fetchall(),
                        [("accepted-original", "running")])
                    self.assertEqual(restored.execute("PRAGMA quick_check").fetchone(), ("ok",))
                finally:
                    restored.close()
                MODULE.verify_bundle(result)
                entries = metadata["recovery_archive_entries"]
                entry = next(item for item in entries if item["archive_path"].endswith("jobs.sqlite3"))
                self.assertEqual(entry["snapshot_method"], "sqlite-online-backup")
                self.assertEqual(entry["validation"], "sqlite-quick-check-ok")
                self.assertNotIn("private", (result / "metadata.json").read_text())
            finally:
                connection.close()

    def test_missing_optional_root_is_recorded_and_required_root_does_not_publish_or_prune(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            stack = self.create_stack(workspace)
            backup_root = workspace / "backups"
            optional = MODULE.OptionalExternalRecoveryRoot("absent", workspace / "absent")
            result = MODULE.run_backup(stack_root=stack, backup_root=backup_root, retain=14,
                runner=FakeRunner(), external_roots=(optional,), now=MODULE.datetime(2026, 10, 10, 3, 30))
            metadata = json.loads((result / "metadata.json").read_text())
            self.assertEqual(metadata["coverage_status"], "partial")
            self.assertEqual(metadata["external_recovery_roots"][0]["status"], "missing")
            required = MODULE.OptionalExternalRecoveryRoot("required", workspace / "absent", required=True)
            with patch.object(MODULE, "prune_completed_backups") as prune:
                with self.assertRaises(FileNotFoundError):
                    MODULE.run_backup(stack_root=stack, backup_root=backup_root, retain=1,
                        runner=FakeRunner(), external_roots=(required,), now=MODULE.datetime(2026, 10, 10, 3, 31))
                prune.assert_not_called()
            self.assertTrue(result.is_dir())
            self.assertEqual([path.name for path in backup_root.iterdir()], [result.name])

    def test_low_disk_space_fails_before_database_dump_or_retention(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            stack = self.create_stack(workspace)
            runner = FakeRunner()
            usage = shutil.disk_usage(workspace)._replace(free=1)
            with patch.object(MODULE.shutil, "disk_usage", return_value=usage), \
                 patch.object(MODULE, "prune_completed_backups") as prune:
                with self.assertRaisesRegex(OSError, "insufficient backup space"):
                    MODULE.run_backup(stack_root=stack, backup_root=workspace / "backups", retain=14, runner=runner)
                self.assertEqual(runner.calls, [])
                prune.assert_not_called()

    def test_external_root_rejects_symlink_ancestor_and_unsafe_archive_names(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            source = workspace / "source"
            source.mkdir()
            (source / "secret").write_text("data")
            link = workspace / "linked"
            try:
                link.symlink_to(source, target_is_directory=True)
            except OSError:
                self.skipTest("directory symlinks are unavailable")
            stack = self.create_stack(workspace)
            for root in (MODULE.OptionalExternalRecoveryRoot("unsafe", link),
                         MODULE.OptionalExternalRecoveryRoot("../escape", source)):
                with self.assertRaises(ValueError):
                    MODULE.run_backup(stack_root=stack, backup_root=workspace / "backups", retain=14,
                        runner=FakeRunner(), external_roots=(root,))

    def test_bundle_validation_rejects_archive_content_even_when_outer_manifest_is_rewritten(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            stack = self.create_stack(workspace)
            result = MODULE.run_backup(stack_root=stack, backup_root=workspace / "backups",
                retain=14, runner=FakeRunner())
            archive = result / "recovery-config.tar.gz"
            with tarfile.open(archive, "w:gz") as handle:
                source = workspace / "injected"
                source.write_text("injected")
                handle.add(source, arcname="../../escape")
            (result / "SHA256SUMS").unlink()
            MODULE.write_manifest(result, [archive, result / "database.pgdump", result / "metadata.json"])
            with self.assertRaises(ValueError):
                MODULE.verify_bundle(result)

    def test_external_config_requires_exact_schema_and_unique_names(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            config = workspace / "roots.json"
            payload = {"schema": "xtai-production-backup-roots-v1", "roots": [
                {"name": "video", "path": str(workspace / "video"), "required": True}]}
            config.write_text(json.dumps(payload))
            os.chmod(config, 0o600)
            roots = MODULE.load_external_recovery_roots(config)
            self.assertEqual(roots[0].name, "video")
            self.assertTrue(roots[0].required)
            payload["roots"].append(payload["roots"][0])
            config.write_text(json.dumps(payload))
            with self.assertRaises(ValueError):
                MODULE.load_external_recovery_roots(config)

    def test_required_sqlite_missing_inside_existing_data_root_refuses_full_bundle(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            stack = self.create_stack(workspace)
            data = workspace / "gateway"
            data.mkdir()
            root = MODULE.OptionalExternalRecoveryRoot("image", data, required=True,
                required_sqlite=("image-jobs.sqlite3",))
            with patch.object(MODULE, "prune_completed_backups") as prune:
                with self.assertRaises(FileNotFoundError):
                    MODULE.run_backup(stack_root=stack, backup_root=workspace / "backups",
                        retain=14, runner=FakeRunner(), external_roots=(root,))
                prune.assert_not_called()
            self.assertEqual(list((workspace / "backups").iterdir()), [])

    def test_runtime_policy_can_explicitly_omit_retired_auxiliaries_but_not_core_dependencies(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            configuration = workspace / "roots.json"
            payload = {"schema": "xtai-production-backup-roots-v1", "roots": [],
                "runtime_containers": list(MODULE.REQUIRED_RUNTIME_CONTAINERS)}
            configuration.write_text(json.dumps(payload))
            os.chmod(configuration, 0o600)
            roots, names = MODULE.load_recovery_scope(configuration)
            self.assertEqual(roots, ())
            self.assertEqual(set(names), set(MODULE.REQUIRED_RUNTIME_CONTAINERS))
            payload["runtime_containers"].remove("ai-api-stack-postgres-1")
            configuration.write_text(json.dumps(payload))
            with self.assertRaises(ValueError):
                MODULE.load_recovery_scope(configuration)

    def test_postgresql_size_rejects_ambiguous_output_without_printing_values(self) -> None:
        for output in (b"", b"0\n", b"-1\n", b"NaN\n", b"private-value\n"):
            with self.subTest(output=output):
                with self.assertRaises(ValueError) as caught:
                    MODULE.postgresql_database_size(Path.cwd(), lambda *args, **kwargs: SimpleNamespace(stdout=output))
                self.assertNotIn("private-value", str(caught.exception))

    def test_runtime_descriptors_are_fresh_private_archived_and_do_not_expose_credentials_in_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            stack = self.create_stack(workspace)
            names = ("ai-api-stack-new-api-1",)
            inspect_calls = []
            fake = FakeRunner()
            container = {"Name": "/" + names[0], "Id": "a" * 64, "Image": "sha256:" + "b" * 64,
                "Config": {"User": "10002:999", "Env": ["API_KEY=secret-never-in-metadata"]},
                "HostConfig": {"ReadonlyRootfs": True}, "Mounts": [{"Source": "/opt/allowed", "Destination": "/data"}],
                "NetworkSettings": {"Networks": {"app-net": {"Aliases": ["current-app"]}}},
                "State": {"Running": True}}

            def run(arguments, **kwargs):
                if arguments[:2] == ["docker", "inspect"]:
                    inspect_calls.append((arguments, kwargs))
                    kwargs["stdout"].write(json.dumps([container]).encode())
                    return None
                return fake(arguments, **kwargs)

            result = MODULE.run_backup(stack_root=stack, backup_root=workspace / "backups",
                retain=14, runner=run, runtime_containers=names)
            metadata = (result / "metadata.json").read_text()
            self.assertNotIn("secret-never", metadata)
            self.assertNotIn("API_KEY", metadata)
            self.assertEqual(len(inspect_calls), 2)
            self.assertEqual(inspect_calls[0][0], ["docker", "inspect", names[0]])
            with tarfile.open(result / "recovery-config.tar.gz", "r:gz") as archive:
                member = archive.getmember("runtime/docker-containers.json")
                self.assertEqual(member.mode, 0o600)
                saved = json.loads(archive.extractfile(member).read())
                self.assertIn("captured_at", saved)
                self.assertEqual(saved["containers"][0]["Config"]["Env"], container["Config"]["Env"])
                self.assertEqual(saved["containers"][0]["Image"], container["Image"])
            MODULE.verify_bundle(result)

    def test_runtime_descriptor_disallowed_target_or_image_drift_refuses_publication(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            stack = self.create_stack(workspace)
            backup_root = workspace / "backups"
            runner = FakeRunner()
            with self.assertRaises(ValueError):
                MODULE.run_backup(stack_root=stack, backup_root=backup_root, retain=14,
                    runner=runner, runtime_containers=("unrelated-container",))
            self.assertEqual(runner.calls, [])
            count = [0]

            def drifting(arguments, **kwargs):
                if arguments[:2] == ["docker", "inspect"]:
                    count[0] += 1
                    kwargs["stdout"].write(json.dumps([{"Name": "/ai-api-stack-new-api-1",
                        "Id": str(count[0]) * 64, "Image": "sha256:" + "b" * 64,
                        "Config": {}, "HostConfig": {}, "Mounts": [],
                        "NetworkSettings": {"Networks": {}}, "State": {"Running": True}}]).encode())
                    return None
                return runner(arguments, **kwargs)

            with patch.object(MODULE, "prune_completed_backups") as prune:
                with self.assertRaisesRegex(RuntimeError, "runtime.*changed"):
                    MODULE.run_backup(stack_root=stack, backup_root=backup_root, retain=14,
                        runner=drifting, runtime_containers=("ai-api-stack-new-api-1",))
                prune.assert_not_called()
            self.assertEqual(list(backup_root.iterdir()), [])

    def test_runtime_identity_ignores_only_full_mount_record_order_and_retains_every_attribute(self) -> None:
        baseline = {"Name": "/ai-api-stack-new-api-1", "Id": "a" * 64,
            "Image": "sha256:" + "b" * 64, "Config": {"User": "10002:999", "Env": ["A=1", "B=2"]},
            "HostConfig": {"ReadonlyRootfs": True},
            "Mounts": [{"Type": "bind", "Source": "/opt/config.json", "Destination": "/config.json",
                "RW": False, "Propagation": "rprivate", "FutureRecoveryAttribute": "preserved"},
                {"Type": "volume", "Name": "persistent", "Source": "/var/lib/docker/volumes/persistent/_data",
                 "Destination": "/data", "RW": True, "Propagation": ""}],
            "NetworkSettings": {"Networks": {"app-net": {"Aliases": ["current-app"],
                "EndpointID": "endpoint-original", "IPAMConfig": {"IPv4Address": "10.1.0.2"}}}}}
        reordered = copy.deepcopy(baseline)
        reordered["Mounts"].reverse()
        self.assertEqual(MODULE._runtime_recovery_identity([baseline]),
            MODULE._runtime_recovery_identity([reordered]))
        for field, value in (("Source", "/opt/other.json"), ("RW", True),
                             ("Propagation", "rshared"), ("FutureRecoveryAttribute", "changed")):
            changed = copy.deepcopy(baseline)
            changed["Mounts"][0][field] = value
            self.assertNotEqual(MODULE._runtime_recovery_identity([baseline]),
                MODULE._runtime_recovery_identity([changed]), field)
        changed = copy.deepcopy(baseline)
        changed["Mounts"].append(copy.deepcopy(changed["Mounts"][0]))
        self.assertNotEqual(MODULE._runtime_recovery_identity([baseline]),
            MODULE._runtime_recovery_identity([changed]))
        for field in ("Id", "Image", "Config", "HostConfig", "NetworkSettings"):
            changed = copy.deepcopy(baseline)
            changed[field] = "different"
            self.assertNotEqual(MODULE._runtime_recovery_identity([baseline]),
                MODULE._runtime_recovery_identity([changed]), field)
        self.assertEqual(baseline["Mounts"][0]["Destination"], "/config.json")

    def test_postgresql_size_preflight_is_readonly_and_checks_headroom_before_dump(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            stack = self.create_stack(workspace)
            calls = []

            def runner(arguments, **kwargs):
                calls.append((arguments, kwargs))
                return SimpleNamespace(stdout=b"4000000\n")

            size = MODULE.postgresql_database_size(stack, runner)
            self.assertEqual(size, 4000000)
            self.assertIn("SELECT pg_database_size(current_database())", calls[0][0][-1])
            usage = shutil.disk_usage(workspace)._replace(free=4000000)
            fake = FakeRunner()
            with patch.object(MODULE.shutil, "disk_usage", return_value=usage):
                with self.assertRaises(OSError):
                    MODULE.run_backup(stack_root=stack, backup_root=workspace / "backups",
                        retain=14, runner=fake, database_size_bytes=size, minimum_free_bytes=0)
            self.assertEqual(fake.calls, [])

    def test_explicit_single_file_and_empty_media_directory_are_recoverable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            stack = self.create_stack(workspace)
            configuration = workspace / "adapter-config.json"
            configuration.write_text('{"provider":"configured"}\n', newline="\n")
            media = workspace / "media"
            media.mkdir()
            (media / "empty-reference-folder").mkdir()
            roots = (MODULE.OptionalExternalRecoveryRoot("adapter", configuration, True),
                MODULE.OptionalExternalRecoveryRoot("media", media, True))
            result = MODULE.run_backup(stack_root=stack, backup_root=workspace / "backups",
                retain=14, runner=FakeRunner(), external_roots=roots)
            metadata = json.loads((result / "metadata.json").read_text())
            self.assertEqual(metadata["coverage_status"], "configured_complete")
            empty_root = next(item for item in metadata["external_recovery_roots"] if item["name"] == "media")
            self.assertTrue(empty_root["is_directory"])
            self.assertEqual(empty_root["captured_files"], 0)
            self.assertIsInstance(empty_root["source_mode"], int)
            self.assertEqual({item["relative_path"] for item in empty_root["directories"]},
                {".", "empty-reference-folder"})
            with tarfile.open(result / "recovery-config.tar.gz", "r:gz") as archive:
                self.assertEqual(archive.extractfile("external/adapter/adapter-config.json").read(),
                    b'{"provider":"configured"}\n')

    def test_source_drift_and_corrupt_sqlite_keep_old_bundle_and_refuse_publication(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            stack = self.create_stack(workspace)
            backup_root = workspace / "backups"
            first = MODULE.run_backup(stack_root=stack, backup_root=backup_root, retain=14,
                runner=FakeRunner(), now=MODULE.datetime(2026, 10, 10, 3, 30))
            external = workspace / "gateway"
            external.mkdir()
            mutable = external / "mutable.json"
            mutable.write_text("before")
            original_copy = MODULE._copy_stable_file

            def mutate_after_capture(source, destination, budget):
                captured = original_copy(source, destination, budget)
                if source == mutable:
                    mutable.write_text("changed-after-snapshot")
                return captured

            roots = (MODULE.OptionalExternalRecoveryRoot("gateway", external, True),)
            with patch.object(MODULE, "_copy_stable_file", side_effect=mutate_after_capture), \
                 patch.object(MODULE, "prune_completed_backups") as prune:
                with self.assertRaisesRegex(RuntimeError, "source changed"):
                    MODULE.run_backup(stack_root=stack, backup_root=backup_root, retain=1,
                        runner=FakeRunner(), external_roots=roots, now=MODULE.datetime(2026, 10, 10, 3, 31))
                prune.assert_not_called()
            (external / "jobs.sqlite3").write_bytes(b"corrupt not a sqlite database")
            with patch.object(MODULE, "prune_completed_backups") as prune:
                with self.assertRaisesRegex(ValueError, "invalid header"):
                    MODULE.run_backup(stack_root=stack, backup_root=backup_root, retain=1,
                        runner=FakeRunner(), external_roots=roots, now=MODULE.datetime(2026, 10, 10, 3, 32))
                prune.assert_not_called()
            self.assertEqual([item.name for item in backup_root.iterdir()], [first.name])
            MODULE.verify_bundle(first)

    def test_source_drift_retains_exact_path_only_on_private_exception_attribute(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            stack = self.create_stack(workspace)
            data = stack / "channel-monitor" / "data"
            data.mkdir(parents=True)
            source = data / "upstream-balance-live.json"
            source.write_text('{"private":"first-snapshot"}')
            original_copy = MODULE._copy_stable_file

            def change_after_capture(path, destination, budget):
                original = original_copy(path, destination, budget)
                if path == source:
                    source.write_text('{"private":"changed-after-snapshot"}')
                return original

            with patch.object(MODULE, "_copy_stable_file", side_effect=change_after_capture), \
                 patch.object(MODULE, "prune_completed_backups") as prune:
                with self.assertRaises(RuntimeError) as caught:
                    MODULE.run_backup(stack_root=stack, backup_root=workspace / "backups",
                        retain=14, runner=FakeRunner())
                prune.assert_not_called()
            self.assertEqual(caught.exception.source_path, source)
            self.assertEqual(caught.exception.phase, "bundle-verification")
            self.assertNotIn(str(source), str(caught.exception))
            self.assertNotIn("first-snapshot", str(caught.exception))
            self.assertNotIn("changed-after-snapshot", str(caught.exception))
            self.assertEqual(list((workspace / "backups").iterdir()), [])

    def test_elapsed_time_and_snapshot_byte_limits_abort_without_retention(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            stack = self.create_stack(workspace)
            clock = [10.0]
            runner = FakeRunner()

            def late_dump(arguments, **kwargs):
                result = runner(arguments, **kwargs)
                clock[0] += 2
                return result

            with patch.object(MODULE.time, "monotonic", side_effect=lambda: clock[0]), \
                 patch.object(MODULE, "prune_completed_backups") as prune:
                with self.assertRaises(TimeoutError):
                    MODULE.run_backup(stack_root=stack, backup_root=workspace / "backups",
                        retain=14, runner=late_dump, snapshot_timeout_seconds=1)
                prune.assert_not_called()
            self.assertEqual(list((workspace / "backups").iterdir()), [])
            with self.assertRaisesRegex(ValueError, "exceeds configured byte limit"):
                MODULE.run_backup(stack_root=stack, backup_root=workspace / "backups",
                    retain=14, runner=FakeRunner(), maximum_snapshot_bytes=1)

    def test_archive_proof_detects_changed_safe_member_and_strict_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            stack = self.create_stack(workspace)
            result = MODULE.run_backup(stack_root=stack, backup_root=workspace / "backups",
                retain=14, runner=FakeRunner())
            archive = result / "recovery-config.tar.gz"
            import io
            with tarfile.open(archive, "w:gz") as handle:
                member = tarfile.TarInfo(".env")
                member.mode = 0o600
                member.uid = member.gid = 0
                member.size = len(b"SECRET=invalid\n")
                handle.addfile(member, io.BytesIO(b"SECRET=invalid\n"))
            manifest = result / "SHA256SUMS"
            manifest.unlink()
            MODULE.write_manifest(result, [archive, result / "database.pgdump", result / "metadata.json"])
            with self.assertRaises(ValueError):
                MODULE.verify_bundle(result)
            manifest.write_text(manifest.read_text() * 2)
            with self.assertRaisesRegex(ValueError, "duplicate"):
                MODULE.verify_manifest(result)

    def test_filesystem_root_or_backup_containing_source_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            stack = self.create_stack(workspace)
            for source in (Path(workspace.anchor), workspace):
                with self.assertRaises(ValueError):
                    MODULE.run_backup(stack_root=stack, backup_root=workspace / "backups",
                        retain=14, runner=FakeRunner(),
                        external_roots=(MODULE.OptionalExternalRecoveryRoot("unsafe", source, True),))

    def test_backup_destination_cannot_chmod_a_filesystem_root_or_stack_ancestor(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            stack = self.create_stack(workspace)
            for destination in (Path(workspace.anchor), workspace, stack):
                with patch.object(MODULE.os, "chmod") as chmod, \
                     patch.object(MODULE.Path, "mkdir", side_effect=AssertionError("unsafe destination reached mkdir")):
                    with self.assertRaises(ValueError):
                        MODULE.run_backup(stack_root=stack, backup_root=destination,
                            retain=14, runner=FakeRunner())
                    chmod.assert_not_called()

    def test_hash_phase_deadline_aborts_before_archive_publication_or_retention(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            stack = self.create_stack(workspace)
            backup_root = workspace / "backups"
            clock = [10.0]
            original_hash = MODULE._sha256

            def expire_during_hash(path, budget=None):
                self.assertIsNotNone(budget, "capture hashes must retain the global deadline")
                clock[0] += 2
                return original_hash(path, budget)

            with patch.object(MODULE.time, "monotonic", side_effect=lambda: clock[0]), \
                 patch.object(MODULE, "_sha256", side_effect=expire_during_hash), \
                 patch.object(MODULE, "prune_completed_backups") as prune:
                with self.assertRaises(TimeoutError):
                    MODULE.run_backup(stack_root=stack, backup_root=backup_root, retain=14,
                        runner=FakeRunner(), snapshot_timeout_seconds=1)
                prune.assert_not_called()
            self.assertEqual(list(backup_root.iterdir()), [])

    def test_sqlite_sidecar_vanishes_between_walk_and_stat_without_losing_database_snapshot(self) -> None:
        for suffix in ("-wal", "-shm", "-journal"):
            with self.subTest(suffix=suffix), tempfile.TemporaryDirectory() as directory:
                workspace = Path(directory)
                stack = self.create_stack(workspace)
                data = workspace / "video"
                data.mkdir()
                database = data / "video-jobs.sqlite3"
                connection = sqlite3.connect(database)
                connection.execute("CREATE TABLE jobs (id TEXT PRIMARY KEY)")
                connection.execute("INSERT INTO jobs VALUES ('original-task')")
                connection.commit()
                connection.close()
                sidecar = Path(str(database) + suffix)
                sidecar.write_bytes(b"temporary-sidecar-fixture")
                original_stat = MODULE._regular_stat
                vanished = [False]

                def disappearing_stat(path):
                    if path == sidecar and not vanished[0]:
                        vanished[0] = True
                        sidecar.unlink()
                    return original_stat(path)

                root = MODULE.OptionalExternalRecoveryRoot("video", data, True,
                    required_sqlite=("video-jobs.sqlite3",))
                with patch.object(MODULE, "_regular_stat", side_effect=disappearing_stat):
                    result = MODULE.run_backup(stack_root=stack, backup_root=workspace / "backups",
                        retain=14, runner=FakeRunner(), external_roots=(root,))
                self.assertTrue(vanished[0])
                MODULE.verify_bundle(result)
                with tarfile.open(result / "recovery-config.tar.gz", "r:gz") as archive:
                    self.assertNotIn("external/video/video-jobs.sqlite3" + suffix, archive.getnames())
                    restored = workspace / "restored.sqlite3"
                    restored.write_bytes(archive.extractfile("external/video/video-jobs.sqlite3").read())
                copied = sqlite3.connect(restored)
                try:
                    self.assertEqual(copied.execute("SELECT id FROM jobs").fetchall(), [("original-task",)])
                    self.assertEqual(copied.execute("PRAGMA quick_check").fetchone(), ("ok",))
                finally:
                    copied.close()

    def test_vanished_secret_and_orphan_sidecars_are_not_silently_ignored(self) -> None:
        for filename in ("secret.json", "orphan.sqlite3-shm", "credential.json-wal"):
            with self.subTest(filename=filename), tempfile.TemporaryDirectory() as directory:
                workspace = Path(directory)
                stack = self.create_stack(workspace)
                data = workspace / "config"
                data.mkdir()
                source = data / filename
                source.write_text("private-fixture")
                original_stat = MODULE._regular_stat

                def disappearing_stat(path):
                    if path == source and source.exists():
                        source.unlink()
                    return original_stat(path)

                with patch.object(MODULE, "_regular_stat", side_effect=disappearing_stat), \
                     patch.object(MODULE, "prune_completed_backups") as prune:
                    with self.assertRaises(FileNotFoundError):
                        MODULE.run_backup(stack_root=stack, backup_root=workspace / "backups",
                            retain=14, runner=FakeRunner(),
                            external_roots=(MODULE.OptionalExternalRecoveryRoot("config", data, True),))
                    prune.assert_not_called()
                self.assertEqual(list((workspace / "backups").iterdir()), [])

    def test_sqlite_sidecar_symlink_is_refused_even_with_a_valid_database(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            stack = self.create_stack(workspace)
            data = workspace / "video"
            data.mkdir()
            database = data / "video-jobs.sqlite3"
            connection = sqlite3.connect(database)
            connection.execute("CREATE TABLE jobs (id TEXT)")
            connection.close()
            target = workspace / "private"
            target.write_text("private-fixture")
            sidecar = Path(str(database) + "-shm")
            try:
                sidecar.symlink_to(target)
            except OSError:
                self.skipTest("symlinks unavailable")
            with self.assertRaises(ValueError):
                MODULE.run_backup(stack_root=stack, backup_root=workspace / "backups",
                    retain=14, runner=FakeRunner(),
                    external_roots=(MODULE.OptionalExternalRecoveryRoot("video", data, True),))

    def test_completed_child_validation_rejects_escape_and_symlink(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            valid = root / "newapi-20260724-033000"
            valid.mkdir()
            self.assertEqual(MODULE.validate_completed_child(root, valid), valid)
            with self.assertRaises(ValueError):
                MODULE.validate_completed_child(root, root.parent / valid.name)
            link = root / "newapi-20260723-033000"
            try:
                link.symlink_to(valid, target_is_directory=True)
            except OSError:
                self.skipTest("directory symlinks are unavailable")
            with self.assertRaises(ValueError):
                MODULE.validate_completed_child(root, link)

    def test_retention_removes_only_old_completed_children(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            names = [
                "newapi-20260720-033000",
                "newapi-20260721-033000",
                "newapi-20260722-033000",
                "newapi-20260723-033000",
            ]
            for name in names:
                (root / name).mkdir()
            (root / ".newapi-20260724-033000.tmp-1").mkdir()
            (root / "manual-keep").mkdir()

            removed = MODULE.prune_completed_backups(root, retain=2)

            self.assertEqual([path.name for path in removed], names[:2])
            self.assertTrue((root / names[2]).is_dir())
            self.assertTrue((root / names[3]).is_dir())
            self.assertTrue((root / "manual-keep").is_dir())
            self.assertTrue((root / ".newapi-20260724-033000.tmp-1").is_dir())

    def test_manifest_detects_changed_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "database.pgdump"
            target.write_bytes(b"PGDMP original")
            MODULE.write_manifest(root, [target])
            MODULE.verify_manifest(root)
            target.write_bytes(b"PGDMP modified")
            with self.assertRaises(ValueError):
                MODULE.verify_manifest(root)

    def test_run_backup_publishes_verified_root_only_bundle(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            stack = workspace / "stack"
            backup_root = workspace / "backups"
            (stack / "nginx" / "conf.d").mkdir(parents=True)
            (stack / "channel-monitor").mkdir()
            (stack / "secrets" / "wechatpay").mkdir(parents=True)
            (stack / ".env").write_text("SECRET=value\n", encoding="utf-8")
            (stack / "docker-compose.yml").write_text("services: {}\n", encoding="utf-8")
            (stack / "nginx" / "conf.d" / "default.conf").write_text("server {}\n", encoding="utf-8")
            (stack / "channel-monitor" / "upstreams.json").write_text("[]\n", encoding="utf-8")
            (stack / "secrets" / "wechatpay" / "key.pem").write_text("private\n", encoding="utf-8")
            runner = FakeRunner()

            result = MODULE.run_backup(
                stack_root=stack,
                backup_root=backup_root,
                retain=14,
                runner=runner,
                now=MODULE.datetime(2026, 7, 24, 3, 30, 0),
            )

            self.assertEqual(result.name, "newapi-20260724-033000")
            self.assertTrue((result / "database.pgdump").is_file())
            self.assertTrue((result / "recovery-config.tar.gz").is_file())
            self.assertTrue((result / "SHA256SUMS").is_file())
            MODULE.verify_manifest(result)
            if os.name != "nt":
                self.assertEqual(os.stat(result).st_mode & 0o777, 0o700)
                for child in result.iterdir():
                    self.assertEqual(os.stat(child).st_mode & 0o777, 0o600)
            self.assertEqual(len(runner.calls), 2)
            dump_hash = hashlib.sha256((result / "database.pgdump").read_bytes()).hexdigest()
            self.assertIn(dump_hash, (result / "SHA256SUMS").read_text(encoding="ascii"))


if __name__ == "__main__":
    unittest.main()
