import importlib.util
import json
import pathlib
import sys
import unittest
import tempfile
from unittest import mock


SPEC_ROOT = pathlib.Path(__file__).resolve().parents[3] / "specs/186-nody-multimodal"
sys.path.insert(0, str(SPEC_ROOT))
spec = importlib.util.spec_from_file_location("operator_canary", SPEC_ROOT / "operator_canary.py")
canary = importlib.util.module_from_spec(spec)
spec.loader.exec_module(canary)


class OperatorCanarySafetyTests(unittest.TestCase):
    def test_baseline_discovery_uses_reviewed_ordinary_user_snapshot_not_service_proxy(self):
        class BaselineCaptured(Exception):pass
        scope=canary.Canary.__new__(canary.Canary)
        scope.receipt="operator-canary-state.json"
        scope.identities=mock.Mock()
        scope.rt=mock.Mock()
        state={"containers":{"public":{"id":"canary-public"},"gateway":{"id":"canary-gateway"}}}
        scope.rt.read.side_effect=[state,[{"key":"isolated-canary-key"}]]
        info={"NetworkSettings":{"Networks":{"app-net":{"IPAddress":"172.18.0.4"}}},"Config":{"Env":["VIDEO_JOB_GATEWAY_TOKEN=private-service"]}}
        scope.rt.inspect.return_value=info
        scope.rt.snapshot.side_effect=BaselineCaptured
        scope.snapshots=mock.Mock(side_effect=AssertionError("service-token baseline discovery is not ordinary-user authentication"))
        with self.assertRaises(BaselineCaptured):scope.exec()
        scope.rt.snapshot.assert_called_once_with(canary.rollout.PUBLIC)
        scope.snapshots.assert_not_called()
    def failed_startup_scope(self, directory):
        scope=canary.Canary.__new__(canary.Canary)
        scope.operation="a"*32
        scope.receipt="operator-canary-state.json"
        scope.data=pathlib.Path(directory)/"operator-canary-data"
        scope.data.mkdir()
        (scope.data/"OWNER.json").write_text(json.dumps({"operation_id":scope.operation}))
        expected={"id":"b"*64,"image":"sha256:"+"c"*64,"name":"xtai-nody-op-gateway-"+"a"*12}
        state={"phase":"created","operation_id":scope.operation,"containers":{"gateway":expected}}
        info={"Id":expected["id"],"Image":expected["image"],"Name":"/"+expected["name"],"State":{"Running":False,"ExitCode":1},"Config":{"Labels":{"com.aixingtuyun.operator-canary":"186","com.aixingtuyun.operator-canary-operation":scope.operation}}}
        scope.rt=mock.Mock(root=pathlib.Path(directory))
        scope.rt.read.side_effect=lambda name:state if name==scope.receipt else [{"user_id":1,"token_id":1},{"user_id":2,"token_id":2}]
        scope.rt.inspect.return_value=info
        wallet={"users":[[1,1000000,0,0],[2,1000000,0,0]],"tokens":[[1,1000000,0],[2,1000000,0]],"tasks":0,"native_tasks":0,"logs":0}
        scope.wallet=mock.Mock(return_value=json.dumps(wallet))
        return scope,state,info,wallet

    def test_explicit_failed_cleanup_accepts_only_missing_database_before_startup(self):
        with tempfile.TemporaryDirectory() as directory:
            scope,_,_,_=self.failed_startup_scope(directory)
            with self.assertRaises(canary.CanaryError):scope.jobs()
            self.assertEqual(scope.jobs(failed_startup=True),0)
            scope.rt.command.assert_not_called()

    def test_running_malformed_owner_extra_data_or_changed_wallet_must_be_preserved(self):
        for mutation in ("running","clean_exit","verified_phase","foreign_label","wrong_owner","extra_data","database_present","wallet_changed"):
            with self.subTest(mutation=mutation),tempfile.TemporaryDirectory() as directory:
                scope,state,info,wallet=self.failed_startup_scope(directory)
                if mutation=="running":info["State"]["Running"]=True
                elif mutation=="clean_exit":info["State"]["ExitCode"]=0
                elif mutation=="verified_phase":state["phase"]="free_verified"
                elif mutation=="foreign_label":info["Config"]["Labels"]["com.aixingtuyun.operator-canary-operation"]="d"*32
                elif mutation=="wrong_owner":(scope.data/"OWNER.json").write_text(json.dumps({"operation_id":"d"*32}))
                elif mutation=="extra_data":(scope.data/"unknown-file").write_text("preserve")
                elif mutation=="database_present":(scope.data/"video-jobs.sqlite3").write_text("preserve")
                else:
                    wallet["users"][0][1]=999999
                    scope.wallet.return_value=json.dumps(wallet)
                with self.assertRaises(canary.CanaryError):scope.jobs(failed_startup=True)
                self.assertTrue(scope.data.exists())
                scope.rt.command.assert_not_called()
    def test_production_and_control_sql_never_accept_hidden_mutation(self):
        scope = canary.Canary.__new__(canary.Canary)
        scope.database = "xtai_nody_op_" + "a" * 12
        scope.rt = mock.Mock()
        for statement, target in (("UPDATE users SET quota=0;", "new-api"),
                                  ("SELECT 1; DROP DATABASE new-api;", "new-api"),
                                  ("DROP DATABASE new-api;", "postgres"),
                                  ("CREATE DATABASE unrelated;", "postgres")):
            with self.subTest(statement=statement), self.assertRaises(canary.CanaryError):
                scope.sql(statement, target)
        scope.rt.command.assert_not_called()
    def test_free_transport_rejects_every_generation_or_unbounded_route_before_network(self):
        for role, method, path in (("public", "POST", "/v1/videos"), ("gateway", "POST", "/v1/videos"),
                                   ("public", "GET", "/v1/videos/job/content"), ("gateway", "GET", "/health"),
                                   ("public", "DELETE", "/ready"), ("public", "GET", "/v1/models?key=secret")):
            with self.subTest(role=role, method=method, path=path), mock.patch.object(canary.http.client, "HTTPConnection") as connection:
                with self.assertRaises(canary.CanaryError):
                    canary.free_request("172.18.0.4", role, method, path)
                connection.assert_not_called()

    def test_dsn_replacement_changes_only_the_explicit_owned_database(self):
        database = "xtai_nody_op_" + "a" * 12
        value = canary.isolated_dsn("postgres://user:private@postgres:5432/new-api?sslmode=disable", database)
        self.assertEqual(value, "postgres://user:private@postgres:5432/" + database + "?sslmode=disable")
        for target in ("new-api", "postgres", "xtai_nody_op_bad;DROP DATABASE new-api"):
            with self.subTest(target=target), self.assertRaises(canary.CanaryError):
                canary.isolated_dsn("postgres://user:private@postgres/new-api", target)

    def test_zero_asset_preflight_fixtures_cannot_create_or_quote_legacy_paid_profiles(self):
        rows = canary.wide_text_fixtures("b" * 32)
        self.assertEqual({row["model"] for row in rows}, set(canary.rollout.NODY_MODELS))
        self.assertEqual(len(rows), 7)
        for row in rows:
            self.assertEqual(row["mode"], "text")
            self.assertFalse(any(row.get(field) for field in ("images", "reference_images", "reference_videos", "reference_audios")))
            self.assertEqual(row["provider_id"], "video-aixingtu-api")

    def test_candidate_resource_identity_rejects_unowned_or_changed_image(self):
        expected = {"id": "a" * 64, "image": "sha256:" + "b" * 64, "name": "xtai-nody-op-public-" + "c" * 12}
        info = {"Id": expected["id"], "Image": expected["image"], "Name": "/" + expected["name"],
                "Config": {"Labels": {"com.aixingtuyun.operator-canary": "186", "com.aixingtuyun.operator-canary-operation": "c" * 32}}}
        canary.assert_owned_container(info, expected, "c" * 32)
        for mutation in ("Id", "Image", "Name"):
            broken = {**info, mutation: "other"}
            with self.subTest(mutation=mutation), self.assertRaises(canary.CanaryError):
                canary.assert_owned_container(broken, expected, "c" * 32)
        info["Config"]["Labels"]["com.aixingtuyun.operator-canary-operation"] = "d" * 32
        with self.assertRaises(canary.CanaryError):
            canary.assert_owned_container(info, expected, "c" * 32)
