"""Explicit Linux-only FREE operator canary: create, exec, cleanup, cleanup_failed.

No generation API is callable. Schema-only PostgreSQL isolation and fresh empty
gateway state are mandatory; private keys/audits stay0600. Importing is inert.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
from decimal import Decimal
import http.client
import ipaddress
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import sqlite3
import subprocess
import sys
import time
import urllib.parse

import rollout_media as rollout


class CanaryError(RuntimeError):
    """Credential-free failure; preserve owned state for explicit recovery."""


def require(ok: bool, message: str) -> None:
    if not ok:
        raise CanaryError(message)


def isolated_dsn(source: str, database: str) -> str:
    """Rewrite only a reviewed production PostgreSQL URL's database component."""
    require(re.fullmatch(r"xtai_nody_op_[0-9a-f]{12}", database) is not None, "Unowned database name")
    parsed = urllib.parse.urlsplit(source)
    require(parsed.scheme in {"postgres", "postgresql"} and parsed.hostname is not None and parsed.path == "/new-api", "Unexpected production DSN")
    return urllib.parse.urlunsplit(parsed._replace(path="/" + database))


def free_request(address: str, role: str, method: str, path: str, *, token: str = "", body: dict | None = None) -> tuple[int, dict]:
    """Deny generation, redirects, queries and all unreviewed HTTP operations."""
    public = method == "GET" and role == "public" and path in {"/ready", "/v1/models", "/v1/capabilities", "/v1/video-prices", "/api/pricing"} and body is None
    validation = method == "POST" and role == "gateway" and path == "/v1/operations/video-input-validation" and isinstance(body, dict)
    require(public or validation, "HTTP route is not a free canary operation")
    require(ipaddress.ip_address(address).is_private, "Canary HTTP target is not a Docker private address")
    connection = http.client.HTTPConnection(address, 8098 if role == "public" else 8091, timeout=20)
    try:
        headers = {"X-XingTu-Contract-Version": "xtai-video-billing-v2.2"}
        if token:
            headers["Authorization"] = "Bearer " + token
        wire = json.dumps(body).encode() if body is not None else None
        if body is not None:
            headers.update({"Content-Type": "application/json", "Idempotency-Key": body["request_id"]})
        connection.request(method, path, body=wire, headers=headers)
        response = connection.getresponse()
        raw = response.read(4 * 1024 * 1024 + 1)
        require(len(raw) <= 4 * 1024 * 1024 and response.status not in {301, 302, 303, 307, 308}, "Free response is oversized or redirects")
        return response.status, json.loads(raw)
    finally:
        connection.close()


def wide_text_fixtures(operation: str) -> list[dict]:
    require(re.fullmatch(r"[0-9a-f]{32}", operation) is not None, "Invalid canary operation")
    tuples = (("wan3.0-video", 3, False), ("wan3.0-video-prime", 3, True), ("omni-flash", 6, True),
              ("flux-3-video", 6, False), ("grok-video-3", 10, True), ("grok-imagine-1.5-video", 7, True), ("grok-imagine-video-official", 2, True))
    return [{"provider_id": "video-aixingtu-api", "request_id": "free-op-" + operation + "-" + str(index), "model": model,
             "mode": "text", "prompt": "Free validation only; never generate.", "resolution": "720p", "duration": duration,
             "aspect_ratio": "16:9", "generate_audio": audio} for index, (model, duration, audio) in enumerate(tuples)]


def assert_owned_container(info: dict, expected: dict, operation: str) -> None:
    labels = info["Config"].get("Labels") or {}
    require(info["Id"] == expected["id"] and info["Image"] == expected["image"] and info["Name"] == "/" + expected["name"]
            and labels.get("com.aixingtuyun.operator-canary") == "186" and labels.get("com.aixingtuyun.operator-canary-operation") == operation,
            "Canary container identity/ownership differs")


class Canary:
    def __init__(self, manifest: dict, release_dir: str, runtime: object | None = None):
        self.manifest = rollout.validate_manifest(manifest)
        self.operation = self.manifest["operation_id"]
        self.release = Path(release_dir)
        require(self.release.as_posix() == "/opt/ai-api-stack/releases/nody-operator-testing-" + self.operation, "Release directory is outside exact scope")
        require(self.manifest.get("allow_untested") is True and len(self.manifest["expected_operator_rules"]) == 7, "Reviewed full manual-testing manifest required")
        self.rt = runtime or rollout.HostRuntime(self.manifest)
        self.manifest_digest = hashlib.sha256(json.dumps(self.manifest,sort_keys=True,separators=(",", ":")).encode()).hexdigest()
        self.database = "xtai_nody_op_" + self.operation[:12]
        self.names = {role: "xtai-nody-op-" + role + "-" + self.operation[:12] for role in ("gateway", "public")}
        self.data = self.rt.root / "operator-canary-data"
        self.receipt = "operator-canary-state.json"

    def sql(self, statement: str, database: str | None = None) -> str:
        """All writes target only this owned canary DB; control DB uses fixed calls."""
        target = database or self.database
        require(target in {self.database, "new-api", "postgres"}, "Unapproved SQL target")
        if target != self.database:
            owned_admin = statement in {"CREATE DATABASE " + self.database + " OWNER newapi;", "DROP DATABASE " + self.database + ";"}
            readonly = statement.startswith("SELECT ") and ";" not in statement.rstrip().rstrip(";")
            require(owned_admin and target == "postgres" or readonly, "Production/control SQL writes are prohibited")
        return self.rt.command(["docker", "exec", "-i", rollout.PG, "psql", "-X", "-v", "ON_ERROR_STOP=1", "-U", "newapi", "-d", target, "-At"], text=statement)

    def identities(self, state: dict) -> None:
        require(state.get("operation_id") == self.operation and state.get("database") == self.database and state.get("manifest_sha256") == self.manifest_digest, "Canary journal differs")
        require(self.rt.root.resolve().as_posix()==self.manifest["private_root"] and self.release.resolve()==self.release,"Reviewed roots contain a symlink or differ")
        native = self.rt.inspect(rollout.NATIVE)
        require(native["Id"] == state["native"]["id"] and native["Image"] == state["native"]["image"] and native["State"]["Running"], "Current native identity changed")
        require(set(state["containers"])=={"public","gateway"},"Canary preparation is incomplete; preserve partial resources")
        for role,item in state["containers"].items():
            info=self.rt.inspect(item["id"])
            assert_owned_container(info, item, self.operation)
            env=rollout.environment(info)
            require(not info["HostConfig"].get("PortBindings"),"Canary unexpectedly publishes host ports")
            if role=="public":
                require(urllib.parse.urlsplit(env.get("SQL_DSN","")).path=="/"+self.database and env.get("LOG_SQL_DSN")=="" and env.get("REDIS_CONN_STRING")=="" and env.get("NODE_TYPE")=="slave" and env.get("QUOTA_DB_AUTHORITATIVE")=="true" and env.get("BATCH_UPDATE_ENABLED")=="false","Public canary wallet/cache isolation differs")
                require(env.get("PUBLIC_VIDEO_NATIVE")==state["native_url"] and env.get("PUBLIC_VIDEO_BACKEND")=="http://"+self.names["gateway"]+":8091" and env.get("PUBLIC_VIDEO_LEGACY")==env.get("PUBLIC_VIDEO_BACKEND"),"Canary routes differ")
            else:
                require(env.get("VIDEO_JOB_GATEWAY_DATA_DIR")=="/data" and env.get("VIDEO_JOB_GATEWAY_ENABLED_PROVIDERS")=="nodyhub" and env.get("VIDEO_JOB_GATEWAY_WEBHOOK_ENABLED")=="0" and env.get("VIDEO_JOB_GATEWAY_PRICING_URL")=="","Gateway canary isolation differs")
                mounts={row["Destination"]:row for row in info["Mounts"]}
                require(mounts["/data"]["Source"]==str(self.data) and mounts["/run/secrets/video-billing"]["Source"]==rollout.SECRETS and mounts["/run/secrets/video-billing"]["RW"] is False,"Gateway data/credential mounts differ")
        require(self.data.is_dir() and not self.data.is_symlink() and json.loads((self.data / "OWNER.json").read_text())["operation_id"] == self.operation, "Canary data ownership differs")
        require(self.sql("SELECT shobj_description(oid,'pg_database') FROM pg_database WHERE datname='" + self.database + "';", "postgres") == "issue186-operator-canary:" + self.operation, "Canary DB ownership comment differs")
        for kind in ("image_profile", "media_profile", "operator_testing_profile"):
            profile = self.manifest[kind]
            require(self.rt.profile_digest(profile["host_path"]) == profile["sha256"], "Reviewed profile changed")

    def wallet(self) -> str:
        return self.sql("SELECT json_build_object('users',(SELECT json_agg(json_build_array(id,quota,used_quota,request_count) ORDER BY id) FROM users),'tokens',(SELECT json_agg(json_build_array(id,remain_quota,used_quota) ORDER BY id) FROM tokens),'tasks',(SELECT count(*) FROM public_video_tasks),'native_tasks',(SELECT count(*) FROM tasks),'logs',(SELECT count(*) FROM logs))::text;")

    def jobs(self, *, failed_startup: bool = False) -> int:
        path = self.data / "video-jobs.sqlite3"
        if failed_startup:
            require(not path.exists(), "Failed-startup cleanup is only for a missing SQLite file")
            state=self.rt.read(self.receipt)
            require(state.get("operation_id")==self.operation and state.get("phase") in {"creating","created"},"Only an unverified startup failure may omit SQLite")
            expected=state["containers"]["gateway"]
            info=self.rt.inspect(expected["id"])
            assert_owned_container(info,expected,self.operation)
            exit_code=info["State"].get("ExitCode")
            require(info["State"].get("Running") is False and type(exit_code) is int and exit_code!=0,"Gateway has not proven a failed startup")
            owner=self.data/"OWNER.json"
            require(self.data.is_dir() and not self.data.is_symlink() and {item.name for item in self.data.iterdir()}=={"OWNER.json"}
                    and owner.is_file() and not owner.is_symlink() and json.loads(owner.read_text()).get("operation_id")==self.operation,"Missing SQLite directory has unowned or additional data")
            accounts=self.rt.read("operator-canary-accounts.json")
            require(isinstance(accounts,list) and len(accounts)==2,"Isolated seed account audit is missing")
            expected_wallet={"users":sorted([[account["user_id"],1000000,0,0] for account in accounts]),
                             "tokens":sorted([[account["token_id"],1000000,0] for account in accounts]),"tasks":0,"native_tasks":0,"logs":0}
            require(json.loads(self.wallet())==expected_wallet,"Failed-startup accounting changed; preserve it")
            return 0
        require(path.is_file() and not path.is_symlink(), "Owned gateway state is missing")
        database=sqlite3.connect(path.as_uri()+"?mode=ro",uri=True,timeout=10)
        try:
            return int(database.execute("SELECT count(*) FROM video_jobs").fetchone()[0])
        finally:
            database.close()

    def dump(self, name: str, *, schema_only: bool = False) -> None:
        target = self.rt.root / name
        fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        arguments = ["docker", "exec", rollout.PG, "pg_dump", "-U", "newapi", "-d", "new-api" if schema_only else self.database]
        arguments += ["--schema-only", "--no-owner", "--no-privileges"] if schema_only else ["-Fc"]
        with os.fdopen(fd, "wb") as output:
            result = subprocess.run(arguments, stdout=output, stderr=subprocess.DEVNULL, timeout=120)
        require(result.returncode == 0 and target.stat().st_size > 0, "Owned/schema-only dump failed")

    def create(self) -> None:
        self.rt.ensure_root()
        require(self.release.is_dir() and not self.release.is_symlink(), "Reviewed release is missing or symlinked")
        require(self.rt.root.resolve().as_posix()==self.manifest["private_root"] and self.release.resolve()==self.release,"Reviewed roots contain a symlink or differ")
        require(not self.rt.exists(self.receipt) and not self.data.exists() and not set(self.names.values()) & set(self.rt.names()), "Canary resources already exist; do not overwrite")
        require(not self.sql("SELECT 1 FROM pg_database WHERE datname='" + self.database + "';", "postgres"), "Canary DB already exists")
        native, public, gateway = self.rt.inspect(rollout.NATIVE), self.rt.inspect(rollout.PUBLIC), self.rt.inspect(rollout.GATEWAYS[0])
        public_env, gateway_env = rollout.environment(public), rollout.environment(gateway)
        require(native["State"]["Running"] and public["State"]["Running"] and gateway["State"]["Running"], "Production baseline is not running")
        dsn = isolated_dsn(public_env["SQL_DSN"], self.database)
        require(public_env.get("VIDEO_JOB_GATEWAY_TOKEN") == gateway_env.get("VIDEO_JOB_GATEWAY_TOKEN") and bool(gateway_env.get("VIDEO_JOB_GATEWAY_TOKEN")), "Production service identity differs")
        for kind in ("image_profile", "media_profile", "operator_testing_profile"):
            profile = self.manifest[kind]
            require(self.rt.profile_digest(profile["host_path"]) == profile["sha256"], "Reviewed profile changed before startup")
        for target in rollout.TARGETS:
            image = self.rt.image(self.manifest["candidate_images"][target])
            label = self.manifest.get("source_labels", {}).get(target, rollout.SOURCE_LABEL)
            require(image["Id"] == self.manifest["candidate_images"][target] and image["Config"].get("Labels", {}).get(label) == self.manifest["candidate_sources"][target], "Candidate image/source identity differs")
        state = {"operation_id": self.operation, "manifest_sha256": self.manifest_digest, "database": self.database, "native_url":public_env["PUBLIC_VIDEO_NATIVE"], "native": {"id": native["Id"], "image": native["Image"]}, "containers": {}, "phase": "creating", "paid_requests": 0}
        self.rt.write(self.receipt, state, exclusive=True)
        self.dump("operator-canary-schema.sql", schema_only=True)
        self.sql("CREATE DATABASE " + self.database + " OWNER newapi;", "postgres")
        self.sql("COMMENT ON DATABASE " + self.database + " IS 'issue186-operator-canary:" + self.operation + "';")
        schema = (self.rt.root / "operator-canary-schema.sql").read_text()
        self.sql(schema)
        require(self.sql("SELECT count(*) FROM users; SELECT count(*) FROM tokens; SELECT count(*) FROM public_video_tasks; SELECT count(*) FROM tasks; SELECT count(*) FROM logs;").splitlines() == ["0"] * 5, "Schema-only clone contains data")
        options = self.sql("SELECT json_build_object('key',key,'value',value)::text FROM options WHERE key IN ('GroupRatio','GroupGroupRatio','UserUsableGroups','AutoGroups');", "new-api")
        for line in options.splitlines():
            item = json.loads(line)
            require(item["key"] in {"GroupRatio", "GroupGroupRatio", "UserUsableGroups", "AutoGroups"}, "Unexpected option source")
            encoded = str(item["value"]).replace("'", "''")
            self.sql("INSERT INTO options(key,value) VALUES ('" + item["key"] + "','" + encoded + "');")
        accounts = []
        for index in (0, 1):
            key, affiliate = secrets.token_hex(24), secrets.token_hex(12)
            uid = self.sql("INSERT INTO users(username,password,role,status,quota,used_quota,request_count,\"group\",aff_code,created_at) VALUES ('noc" + self.operation[:8] + str(index) + "','!canary-no-login',1,1,1000000,0,0,'auto','" + affiliate + "',extract(epoch from now())::bigint) RETURNING id;").splitlines()[0]
            group = "" if index == 0 else "nody-denied-" + self.operation[:12]
            tid = self.sql("INSERT INTO tokens(user_id,key,status,name,created_time,accessed_time,expired_time,remain_quota,unlimited_quota,model_limits_enabled,model_limits,allow_ips,used_quota,\"group\",cross_group_retry) VALUES (" + str(int(uid)) + ",'" + key + "',1,'operator-canary',extract(epoch from now())::bigint,0,-1,1000000,false,false,'','',0,'" + group + "',false) RETURNING id;").splitlines()[0]
            accounts.append({"user_id": int(uid), "token_id": int(tid), "key": "sk-" + key})
        self.rt.write("operator-canary-accounts.json", accounts, exclusive=True)
        self.data.mkdir(mode=0o700); os.chown(self.data, 10002, 999)
        owner = self.data / "OWNER.json"
        fd = os.open(owner, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as output: json.dump({"operation_id": self.operation}, output)
        values = {key: value for key, value in gateway_env.items() if key.startswith(("VIDEO_JOB_GATEWAY_", "VIDEO_JOB_NODYHUB_")) or key == "TZ"}
        values.update(VIDEO_JOB_GATEWAY_ENABLED_PROVIDERS="nodyhub", VIDEO_JOB_GATEWAY_V21_APPROVED_PROVIDERS="nodyhub", VIDEO_JOB_GATEWAY_DATA_DIR="/data", VIDEO_JOB_GATEWAY_WEBHOOK_ENABLED="0", VIDEO_JOB_GATEWAY_WEBHOOK_URL="", VIDEO_JOB_GATEWAY_WEBHOOK_SECRET="", VIDEO_JOB_GATEWAY_PRICING_URL="", VIDEO_JOB_GATEWAY_PUBLIC_BASE_URL="https://operator-canary.invalid", VIDEO_JOB_NODYHUB_BILLING_ENABLED="1", **self.manifest["gateway_env"])
        values["VIDEO_JOB_NODYHUB_IMAGE_CONTRACT_FILE"] = self.manifest["image_profile"]["container_path"]
        public_values = {key: value for key, value in public_env.items() if key in {"SESSION_SECRET", "CRYPTO_SECRET", "TZ", "PUBLIC_VIDEO_QUOTA_PER_CNY", "PUBLIC_VIDEO_NATIVE"}}
        public_values.update(SQL_DSN=dsn, LOG_SQL_DSN="", NODE_TYPE="slave", REDIS_CONN_STRING="", QUOTA_DB_AUTHORITATIVE="true", BATCH_UPDATE_ENABLED="false", VIDEO_JOB_GATEWAY_TOKEN=values["VIDEO_JOB_GATEWAY_TOKEN"], PUBLIC_VIDEO_BACKEND="http://" + self.names["gateway"] + ":8091", PUBLIC_VIDEO_LEGACY="http://" + self.names["gateway"] + ":8091", PUBLIC_VIDEO_BASE_URL="https://operator-canary.invalid", PUBLIC_VIDEO_TRUSTED_PROXIES="127.0.0.1")
        for role, environment in (("gateway", values), ("public", public_values)):
            path = self.rt.root / ("operator-canary-" + role + ".env")
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w") as output:
                for key, value in sorted(environment.items()):
                    require(isinstance(value,str) and "\n" not in value and "\r" not in value, "Invalid private environment")
                    output.write(key + "=" + value + "\n")
            target = rollout.GATEWAYS[0] if role == "gateway" else rollout.PUBLIC
            networks = {"app-net": {}} if role == "gateway" else {name: {} for name in public["NetworkSettings"]["Networks"]}
            require("app-net" in networks, "Canary network is missing")
            config = copy.deepcopy(self.rt.image(self.manifest["candidate_images"][target])["Config"])
            config.update(Image=self.manifest["candidate_images"][target], Env=[key + "=" + value for key,value in environment.items()], Labels={"com.aixingtuyun.operator-canary":"186","com.aixingtuyun.operator-canary-operation":self.operation}, NetworkingConfig={"EndpointsConfig":networks}, HostConfig={"NetworkMode":"app-net","Memory":1024*1024*1024,"RestartPolicy":{"Name":"no"},"Binds":[str(self.data)+":/data",rollout.SECRETS+":/run/secrets/video-billing:ro"] if role=="gateway" else []})
            if role == "gateway": config["User"] = "10002:999"
            identity = self.rt.create(self.names[role],config)
            state["containers"][role] = {"id":identity,"name":self.names[role],"image":self.manifest["candidate_images"][target]}
            self.rt.write(self.receipt,state)
            self.rt.start(identity)
        state["phase"]="created"; self.rt.write(self.receipt,state)

    def snapshots(self, address: str, token: str) -> dict:
        result = {}
        for path in ("/ready","/v1/models","/v1/capabilities","/v1/video-prices","/api/pricing"):
            status, value = free_request(address,"public","GET",path,token=token)
            require(status==200,"Free catalog is not ready")
            result[path]=value
        return result

    def exec(self) -> None:
        state = self.rt.read(self.receipt); self.identities(state)
        accounts = self.rt.read("operator-canary-accounts.json")
        public = self.rt.inspect(state["containers"]["public"]["id"])
        gateway = self.rt.inspect(state["containers"]["gateway"]["id"])
        address = public["NetworkSettings"]["Networks"]["app-net"]["IPAddress"]
        gateway_address = gateway["NetworkSettings"]["Networks"]["app-net"]["IPAddress"]
        token = rollout.environment(self.rt.inspect(rollout.GATEWAYS[0]))["VIDEO_JOB_GATEWAY_TOKEN"]
        baseline = self.rt.snapshot(rollout.PUBLIC)
        for attempt in range(10):
            try: observed = self.snapshots(address,accounts[0]["key"]); break
            except (OSError,ValueError,CanaryError):
                if attempt==9: raise
                time.sleep(1)
        before, jobs = self.wallet(), self.jobs()
        require(jobs==0,"Canary gateway is not empty")
        require(free_request(address,"public","GET","/v1/models")[0]==401,"Anonymous model discovery was accepted")
        require(free_request(address,"public","GET","/v1/models",token=accounts[1]["key"])[0]==401,"Denied canary token group was accepted")
        for payload in wide_text_fixtures(self.operation):
            status, quote = free_request(gateway_address,"gateway","POST","/v1/operations/video-input-validation",token=token,body=payload)
            require(status==200 and quote.get("ok") is True and quote.get("task_created") is False and quote.get("upstream_submitted") is False and quote.get("billing_contract_version")=="xtai-video-billing-v2.2" and quote.get("currency")=="CNY" and quote.get("admission_mode")=="operator_testing" and quote.get("price_source")=="nodyhub_operator_testing_estimate" and quote.get("pricing_kind")=="estimated_reservation" and quote.get("verification_status")=="unverified" and quote.get("is_upper_bound") is False and type(quote.get("reserve_cap_applied")) is bool and re.fullmatch(r"[0-9a-f]{64}",str(quote.get("policy_digest"))) is not None,"Free preflight contract differs")
            require(all(quote.get(key)==payload[key] for key in ("model","mode","resolution","duration","aspect_ratio","generate_audio")) and all(quote.get(key)==0 for key in ("image_count","video_count","audio_count")) and 0<Decimal(quote["reserved_cny_exact"])<=150,"Preflight tuple/amount differs")
        for snapshot in (baseline,observed):
            snapshot["/v1/capabilities"]["capabilities"]["video"]["models"] = [row for row in snapshot["/v1/capabilities"]["capabilities"]["video"]["models"] if row["id"] in rollout.NODY_MODELS]
            snapshot["/v1/video-prices"]["pricing"]["models"] = [row for row in snapshot["/v1/video-prices"]["pricing"]["models"] if row["model"] in rollout.NODY_MODELS]
            snapshot["/v1/models"]["data"] = [row for row in snapshot["/v1/models"]["data"] if row["id"] in rollout.NODY_MODELS]
        rollout.compare_snapshot(baseline,observed,self.manifest["expected_media_prices"],operator_rules=self.manifest["expected_operator_rules"])
        require({row["id"] for row in observed["/v1/models"]["data"]}==set(rollout.NODY_MODELS),"Canary model set differs")
        market={row["model_name"]:row for row in observed["/api/pricing"]["data"]}
        for rule in self.manifest["expected_operator_rules"]:
            row=market[rule["model"]];operator=row.get("operator_testing",{})
            require(operator.get("available") is True and operator.get("verification_status")=="unverified" and operator.get("pricing_kind")=="estimated_reservation" and operator.get("is_upper_bound") is False and rollout.canonical_rows(operator.get("rules",[]))==rollout.canonical_rows([rule]) and not rollout.contains_private_fields(operator),"Marketplace operator projection differs")
            for kind,count in (("reference_video",rule["max_videos"]),("reference_audio",rule["max_audios"])):
                if count:
                    capability=row.get(kind,{})
                    require(capability.get("available") is True and capability.get("verification_status")=="unverified" and capability.get("admission_mode")=="operator_testing" and capability.get("max_count")==count,"Marketplace candidate AV flags differ")
        require(before==self.wallet() and self.jobs()==0,"Free checks changed wallet/task/log accounting")
        self.identities(state)
        self.rt.write("operator-canary-free-audit.json",{"before":before,"after":self.wallet(),"jobs":0,"baseline":baseline,"observed":observed,"preflight_count":7,"paid_requests":0,"passed":True})
        state["phase"]="free_verified"; self.rt.write(self.receipt,state)

    def cleanup(self, *, failed_startup: bool = False) -> None:
        state = self.rt.read(self.receipt); self.identities(state)
        wallet = json.loads(self.wallet())
        require(self.jobs(failed_startup=failed_startup)==0 and wallet["tasks"]==0 and wallet["native_tasks"]==0 and wallet["logs"]==0,"Nonempty canary must be preserved")
        self.rt.write("operator-canary-cleanup-audit.json",{"state":state,"wallet":self.wallet(),"jobs":0,"failed_startup":failed_startup,"sqlite_missing":failed_startup},exclusive=True)
        self.dump("operator-canary-final.dump")
        if not failed_startup:
            source = sqlite3.connect((self.data/"video-jobs.sqlite3").as_uri()+"?mode=ro",uri=True,timeout=10)
            target = self.rt.root/"operator-canary-final.sqlite3"
            fd=os.open(target,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600);os.close(fd)
            output=sqlite3.connect(target)
            try: source.backup(output);require(output.execute("PRAGMA integrity_check").fetchone()[0]=="ok","Owned SQLite audit integrity failed")
            finally: output.close();source.close()
        for item in state["containers"].values():
            assert_owned_container(self.rt.inspect(item["id"]),item,self.operation)
            self.rt.stop(item["id"])
        require(self.jobs(failed_startup=failed_startup)==0, "Owned gateway changed during stop")
        require(json.loads(self.wallet())==wallet,"Canary accounting changed during stop")
        require(self.sql("SELECT shobj_description(oid,'pg_database') FROM pg_database WHERE datname='"+self.database+"';","postgres")=="issue186-operator-canary:"+self.operation,"DB ownership changed before cleanup")
        for item in state["containers"].values():
            assert_owned_container(self.rt.inspect(item["id"]),item,self.operation)
            self.rt.command(["docker","rm",item["id"]])
        require(self.sql("SELECT count(*) FROM pg_stat_activity WHERE datname='"+self.database+"';","postgres")=="0","Owned database still has sessions; preserve backup")
        self.sql("DROP DATABASE "+self.database+";","postgres")
        require(self.data.resolve()==(self.rt.root/"operator-canary-data").resolve() and not any(path.is_symlink() for path in self.data.rglob("*")),"Data cleanup target differs")
        shutil.rmtree(self.data)
        for role in ("gateway", "public"):
            path = self.rt.root / ("operator-canary-"+role+".env")
            require(path.is_file() and not path.is_symlink(), "Owned private environment cleanup differs")
            path.unlink()
        state["phase"]="cleaned"; self.rt.write(self.receipt,state)

    def cleanup_failed(self) -> None:
        """Explicit cleanup for a labeled failed process that never created state."""
        self.cleanup(failed_startup=True)


def main() -> None:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action",choices=("create","exec","cleanup","cleanup_failed"));parser.add_argument("--manifest",required=True);parser.add_argument("--release-dir",required=True)
    args=parser.parse_args()
    require(sys.platform.startswith("linux"),"Server-local Linux helper only")
    manifest_path=Path(args.manifest)
    require(manifest_path.is_file() and not manifest_path.is_symlink() and manifest_path.stat().st_size<=2*1024*1024,"Invalid manifest file")
    canary=Canary(json.loads(manifest_path.read_text()),args.release_dir)
    require(manifest_path.resolve().parent==canary.rt.root.resolve() or manifest_path.resolve().parent==canary.release.resolve(),"Manifest is outside reviewed operation/release")
    getattr(canary,args.action)()
    print(json.dumps({"action":args.action,"operation_id":canary.operation,"paid_requests":0,"passed":True}))


if __name__=="__main__":
    try: main()
    except Exception as error:
        print(json.dumps({"needs_attention":True,"error_type":type(error).__name__,"paid_requests":0,"preserved_state":True}));raise SystemExit(1)
