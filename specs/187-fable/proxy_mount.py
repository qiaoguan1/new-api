"""Explicitly authorized, proxy-only repair of the issue187 single-file bind.

Run on the production host only after independent review:
    python3 proxy_mount.py maintain

Changes only the nginx service's RO default-file bind to a RO directory bind
and adds a task-owned RO nginx.conf that includes default.conf alone. Dormant
configurations are preserved but never loaded. No model, SQL, native runtime,
gateway configuration, wallet or token is written. Pending graceful shutdown
and interrupted phases require reconciliation, not automatic replay.
"""
import argparse
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import posixpath
import re
import secrets
import sqlite3
import subprocess
import time
import urllib.request

ROOT = Path("/opt/ai-api-stack/backups/fable187-20261007")
STACK = Path("/opt/ai-api-stack")
BASE = STACK / "docker-compose.yml"
OVERRIDE = STACK / "docker-compose.override.yml"
CONFIG_DIR = STACK / "nginx/conf.d"
DEFAULT = CONFIG_DIR / "default.conf"
STATIC_MAIN = STACK / "nginx/issue187-main-nginx.conf"
FILE_TARGET = "/etc/nginx/conf.d/default.conf"
DIRECTORY_TARGET = "/etc/nginx/conf.d"
MAIN_TARGET = "/etc/nginx/nginx.conf"
NGINX = "ai-api-stack-nginx-1"
NATIVE = "ai-api-stack-new-api-1"
GATEWAY = "xtai-image-job-gateway-image-job-gateway-1"
GATEWAY_ROOT = Path("/opt/xtai-image-job-gateway")
DRAIN = GATEWAY_ROOT / "data/DRAIN"
DRAIN_OWNER = "issue187-approved-proxy-mount"
BASELINE_IMAGE = "sha256:8ac4d2a3590c34852de81b5e576c8212bc91304a35fbbbccd03bff117d794c97"
NGINX_IMAGE = "sha256:5616878291a2eed594aee8db4dade5878cf7edcb475e59193904b198d9b830de"


class ProxyMaintenanceError(RuntimeError):
    """Credential-free failure, safe for the caller's status report."""


def require(condition: bool, message: str) -> None:
    """Fail closed with a deliberately credential-free condition description."""
    if not condition:
        raise ProxyMaintenanceError(message)


def private_write(path: Path, value: object) -> None:
    """Exclusively create and fsync private evidence, never overwrite a phase."""
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)
        handle.flush()
        os.fsync(handle.fileno())
    os.chmod(path, 0o600)


def yaml_document(text: str) -> dict:
    """Reject duplicate keys that could hide an unrelated compose mutation."""
    import yaml

    class UniqueLoader(yaml.SafeLoader):
        pass

    def mapping(loader, node, deep=False):
        keys = [loader.construct_object(key, deep=deep) for key, _ in node.value]
        require(len(keys) == len(set(keys)), "duplicate compose mapping key")
        return yaml.SafeLoader.construct_mapping(loader, node, deep=deep)

    UniqueLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, mapping)
    value = yaml.load(text, Loader=UniqueLoader)
    require(isinstance(value, dict), "compose document missing")
    return value


def volume_row(value: object) -> tuple:
    """Normalize Linux compose bind syntax without guessing a volume type."""
    if isinstance(value, str):
        fields = value.split(":")
        require(len(fields) in {2, 3}, "unsupported compose volume syntax")
        options = fields[2].split(",") if len(fields) == 3 else []
        require(not ({"ro", "rw"} <= set(options)), "ambiguous volume permissions")
        return fields[0], fields[1], "ro" in options
    require(isinstance(value, dict) and value.get("type") == "bind", "unsupported compose volume type")
    return value.get("source"), value.get("target"), value.get("read_only") is True


def bind_source(source: str) -> str:
    """Resolve Linux relative bind paths against the fixed compose project root."""
    require(isinstance(source, str) and bool(source), "bind source missing")
    source_path = Path(source).as_posix()
    normalized = posixpath.normpath(source_path if source_path.startswith("/") else STACK.as_posix() + "/" + source_path)
    return str(Path(normalized))


def change_proxy_mounts(base_text: str, override_text: str, static_path: Path) -> tuple:
    """Change exactly two volume values in the owning nginx compose document."""
    import yaml
    documents = [yaml_document(base_text), yaml_document(override_text)]
    changed = copy.deepcopy(documents)
    matches = []
    for document_index, document in enumerate(documents):
        service = document.get("services", {}).get("nginx", {})
        require(isinstance(service, dict), "nginx compose service invalid")
        for volume_index, value in enumerate(service.get("volumes", [])):
            source, target, readonly = volume_row(value)
            require(target not in {MAIN_TARGET, DIRECTORY_TARGET}, "existing nginx main/directory bind requires review")
            if target == FILE_TARGET or bind_source(source) == str(DEFAULT):
                require((bind_source(source), target, readonly) == (str(DEFAULT), FILE_TARGET, True), "nginx source/target/RO bind drift")
                matches.append((document_index, volume_index))
    require(len(matches) == 1, "exactly one nginx default single-file bind required")
    document_index, volume_index = matches[0]
    volumes = changed[document_index]["services"]["nginx"]["volumes"]
    value = volumes[volume_index]
    if isinstance(value, str):
        fields = value.split(":")
        volumes[volume_index] = str(CONFIG_DIR) + ":" + DIRECTORY_TARGET + ":" + fields[2]
        volumes.append(str(static_path) + ":" + MAIN_TARGET + ":ro")
    else:
        value.update(source=str(CONFIG_DIR), target=DIRECTORY_TARGET)
        volumes.append({"type": "bind", "source": str(static_path), "target": MAIN_TARGET, "read_only": True})
    check = copy.deepcopy(changed)
    check[document_index]["services"]["nginx"]["volumes"][volume_index] = documents[document_index]["services"]["nginx"]["volumes"][volume_index]
    check[document_index]["services"]["nginx"]["volumes"].pop()
    require(check == documents, "compose mutation exceeds authorized proxy mounts")
    outputs = [yaml.safe_dump(value, allow_unicode=True, sort_keys=False) for value in changed]
    require([yaml_document(text) for text in outputs] == changed, "compose serialization changed unrelated semantics")
    return tuple(outputs)


def static_main(text: str) -> str:
    """Pin the sole active include, keeping every other configuration byte."""
    pattern = re.compile(r"\binclude[ \t]+(/etc/nginx/conf\.d/\*\.conf)[ \t]*;")
    matches = [match for match in pattern.finditer(text)
               if "#" not in text[text.rfind("\n", 0, match.start())+1:match.start()]]
    require(len(matches) == 1, "exactly one active nginx conf.d wildcard include required")
    match = matches[0]
    return text[:match.start(1)] + FILE_TARGET + text[match.end(1):]


def check_loaded_configuration(before: str, after: str) -> None:
    """Reject extra loaded virtual hosts or any unintended effective config."""
    for text in [before, after]:
        paths = re.findall(r"(?m)^# configuration file (/etc/nginx/conf\.d/[^:]+):$", text)
        require(paths == [FILE_TARGET], "unexpected loaded nginx conf.d files")
    require(static_main(before) == after, "loaded nginx configuration changed beyond the fixed include")


def check_resolved_compose(before: dict, after: dict) -> None:
    """Resolved compose must change only the two approved nginx mounts."""
    before = copy.deepcopy(before)
    expected = copy.deepcopy(after)
    values = expected.get("services", {}).get("nginx", {}).get("volumes", [])
    directory = [row for row in values if row.get("target") == DIRECTORY_TARGET]
    main = [row for row in values if row.get("target") == MAIN_TARGET]
    require(len(directory) == len(main) == 1 and
            volume_row(directory[0]) == (str(CONFIG_DIR), DIRECTORY_TARGET, True) and
            volume_row(main[0]) == (str(STATIC_MAIN), MAIN_TARGET, True), "resolved proxy replacement mounts invalid")
    old = [row for row in before.get("services", {}).get("nginx", {}).get("volumes", []) if row.get("target") == FILE_TARGET]
    require(len(old) == 1 and volume_row(old[0]) == (str(DEFAULT), FILE_TARGET, True), "resolved baseline file bind invalid")
    directory_index = values.index(directory[0])
    values[directory_index] = copy.deepcopy(old[0])
    values.remove(main[0])
    # Compose emits a target-sorted volume list; comparison is semantic.
    for document in [expected, before]:
        document["services"]["nginx"]["volumes"] = sorted(document["services"]["nginx"]["volumes"], key=lambda row: row["target"])
    require(expected == before, "resolved compose changed unrelated services or proxy settings")


def mount_signature(info: dict) -> list:
    """Return stable source/target/type/permissions signatures from inspection."""
    return sorted([(row.get("Type"), row.get("Source"), row.get("Destination"), row.get("RW"), row.get("Propagation"))
                   for row in info.get("Mounts", [])], key=lambda row: row[2])


def replacement_mounts(before: dict, *, directory: bool) -> list:
    """Derive the two authorized mount deltas without changing other mounts."""
    expected = mount_signature(before)
    require(not any(row[2] in {DIRECTORY_TARGET, MAIN_TARGET} for row in expected), "original proxy main/directory bind drift")
    old = [row for row in expected if row[2] == FILE_TARGET]
    require(len(old) == 1 and old[0][:4] == ("bind", str(DEFAULT), FILE_TARGET, False), "original proxy file bind drift")
    if directory:
        expected.remove(old[0])
        expected.extend([("bind", str(CONFIG_DIR), DIRECTORY_TARGET, False, old[0][4]),
                         ("bind", str(STATIC_MAIN), MAIN_TARGET, False, "rprivate")])
    return sorted(expected, key=lambda row: row[2])


def environment_map(values: object) -> dict[str, str]:
    """Validate every env field and compare names/values independently of order.

    Duplicate names are ambiguous even when their values match. Non-string,
    missing-name, missing-equals and NUL-bearing fields are rejected; values
    (including empty strings and embedded equals signs) remain byte-for-byte.
    """
    require(isinstance(values, list), "nginx environment field list invalid")
    result = {}
    for entry in values:
        require(isinstance(entry, str) and "=" in entry and "\x00" not in entry,
                "nginx environment field malformed")
        name, value = entry.split("=", 1)
        require(bool(name) and name not in result, "nginx environment name missing or duplicated")
        result[name] = value
    return result


def check_recreated_proxy(before: dict, after: dict, *, directory: bool) -> None:
    """Verify proxy identity/settings and unchanged non-target mounts."""
    require(after.get("State", {}).get("Running") is True and after.get("Image") == before.get("Image"), "nginx image/readiness drift")
    require(environment_map(after.get("Config", {}).get("Env")) == environment_map(before.get("Config", {}).get("Env")),
            "nginx environment names or values drift")
    for key in ["Cmd", "User", "WorkingDir", "Entrypoint", "StopSignal", "ExposedPorts"]:
        require(after.get("Config", {}).get(key) == before.get("Config", {}).get(key), "nginx process settings drift")
    require(set(after.get("NetworkSettings", {}).get("Networks", {})) == set(before.get("NetworkSettings", {}).get("Networks", {})),
            "nginx networks drift")
    for key in ["PortBindings", "RestartPolicy", "ReadonlyRootfs", "Privileged", "CapAdd", "CapDrop", "SecurityOpt", "ExtraHosts", "Dns", "DnsSearch"]:
        expected = before.get("HostConfig", {}).get(key)
        actual = after.get("HostConfig", {}).get(key)
        if key in {"ExtraHosts", "Dns", "DnsSearch"}:
            expected, actual = optional_host_list(expected), optional_host_list(actual)
        require(actual == expected, "nginx host settings drift")
    require(mount_signature(after) == replacement_mounts(before, directory=directory), "nginx unrelated mount or RO permission drift")


def service_identity(info: dict) -> dict:
    """A private baseline identity for services this helper must never restart."""
    return {"id": info.get("Id"), "image": info.get("Image"), "env": info.get("Config", {}).get("Env"),
            "started": info.get("State", {}).get("StartedAt"),
            "networks": info.get("NetworkSettings", {}).get("Networks"), "mounts": [list(row) for row in mount_signature(info)]}


def optional_host_list(value) -> list:
    """Docker represents absent explicit DNS/host overrides as null or []."""
    require(value is None or isinstance(value, list), "invalid explicit host override list")
    values = [] if value is None else value
    require(all(isinstance(item, str) for item in values), "invalid explicit host override value")
    return values


class ProxyMountMaintenance:
    """Durable, guarded proxy-only maintenance with no forced shutdown."""

    def __init__(self, root: Path = ROOT):
        self.root = Path(root)
        self._runtime = None
        self._index = 0
        self._invocation = secrets.token_hex(8)
        self._compose_expected = None
        self._drain_identity = None
        self._drain_complete = False

    @property
    def runtime(self):
        if self._runtime is None:
            spec = importlib.util.spec_from_file_location("proxy_runtime", Path(__file__).with_name("deploy_runtime.py"))
            require(spec is not None and spec.loader is not None, "runtime helper unavailable")
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            self._runtime = module.RuntimeDeployment(root=self.root)
        return self._runtime

    @property
    def h(self):
        return self.runtime.h

    def run(self, command: list, timeout: int = 30) -> str:
        """Keep command output and exceptions private; emit no env or configs."""
        self._index += 1
        path = self.root / ("proxy-command-" + self._invocation + "-" + str(self._index) + ".json")
        try:
            process = subprocess.run(command, capture_output=True, text=True, timeout=timeout)
            private_write(path, {"returncode": process.returncode, "stdout": process.stdout, "stderr": process.stderr})
        except Exception as error:
            private_write(path, {"exception_type": type(error).__name__})
            raise ProxyMaintenanceError("proxy command incomplete; inspect private evidence") from None
        require(process.returncode == 0, "proxy command failed; inspect private evidence")
        return process.stdout

    def resolve_compose(self) -> dict:
        """Read the effective fixed-stack compose without starting any service."""
        return json.loads(self.run(["docker", "compose", "-f", str(BASE), "-f", str(OVERRIDE), "config", "--format", "json"]))

    def preflight(self) -> tuple:
        """Prove exact existing state before any host file or service change."""
        require(not any((self.root/name).exists() for name in ["proxy-mount-started.json", "proxy-mount-verified.json"]),
                "proxy phase already started; reconcile instead of replaying")
        require(not DRAIN.exists() and not DRAIN.is_symlink(), "pre-existing image DRAIN requires review")
        require(DEFAULT.is_file() and not DEFAULT.is_symlink() and not CONFIG_DIR.is_symlink(), "canonical default config invalid")
        require(not STATIC_MAIN.exists() and not STATIC_MAIN.is_symlink(), "task-owned main config already exists; reconcile")
        candidate = self.runtime.rollout.load_candidate()
        self.runtime_module_verify(candidate)
        native, gateway, nginx = self.h.inspect(NATIVE), self.h.inspect(GATEWAY), self.h.inspect(NGINX)
        require(native.get("Image") == BASELINE_IMAGE and native.get("State", {}).get("Running") is True,
                "native baseline changed before proxy maintenance")
        require(nginx.get("Image") == NGINX_IMAGE, "authorized nginx immutable baseline changed")
        check_recreated_proxy(nginx, nginx, directory=False)
        require(nginx.get("Config", {}).get("StopSignal") in {"SIGQUIT", "QUIT", "3"}, "nginx graceful StopSignal not verified")
        require(nginx.get("HostConfig", {}).get("RestartPolicy") == {"Name": "unless-stopped", "MaximumRetryCount": 0},
                "nginx original restart policy not verified")
        require(nginx.get("Config", {}).get("User", "") in {"", "root", "0", "0:0"}, "private nginx.conf unreadable by configured container user")
        self.run(["docker", "exec", NGINX, "nginx", "-t"])
        main = self.run(["docker", "exec", NGINX, "cat", MAIN_TARGET])
        loaded = self.run(["docker", "exec", NGINX, "nginx", "-T"])
        default = DEFAULT.read_text()
        require(self.run(["docker", "exec", NGINX, "cat", FILE_TARGET]) == default, "host/default bound inode content mismatch; maintenance prohibited")
        changed_main = static_main(main)
        check_loaded_configuration(loaded, static_main(loaded))
        compose = (BASE.read_text(), OVERRIDE.read_text())
        changed = change_proxy_mounts(*compose, STATIC_MAIN)
        resolved = self.resolve_compose()
        require(self.run(["docker", "image", "inspect", "--format", "{{.Id}}", resolved["services"]["nginx"]["image"]]).strip() == nginx["Image"],
                "resolved compose would change nginx image")
        image_info = json.loads(self.run(["docker", "image", "inspect", nginx["Image"]]))[0]
        environment = environment_map(image_info["Config"].get("Env", []))
        environment.update({key: str(value) for key, value in resolved["services"]["nginx"].get("environment", {}).items()})
        require(environment == environment_map(nginx["Config"].get("Env", [])), "compose would change nginx environment")
        before = {"base_compose": compose[0], "override_compose": compose[1], "resolved_compose": resolved,
                  "nginx_inspect": nginx, "main": main, "default": default, "loaded": loaded,
                  "native_identity": service_identity(native), "gateway_identity": service_identity(gateway), "candidate": candidate,
                  "dormant_conf_files": sorted(path.name for path in CONFIG_DIR.glob("*.conf") if path.name != "default.conf")}
        self._compose_expected = compose
        return before, changed, changed_main

    def runtime_module_verify(self, candidate: dict) -> None:
        """Reuse the image-bound, already-reviewed issue187 acceptance gate."""
        spec = importlib.util.spec_from_file_location("proxy_runtime_proofs", Path(__file__).with_name("deploy_runtime.py"))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        module.verify_proofs(candidate, self.runtime.rollout.read("validation-pipeline-result.json"),
                             self.runtime.rollout.read("fake-verified.json"), self.runtime.rollout.read("real-verified.json"))

    def backup(self, before: dict, changed_main: str) -> None:
        """Persist original files/inspection and exclusive task-owned main copy."""
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(self.root, 0o700)
        private_write(self.root/"proxy-mount-started.json", {"at": int(time.time()), "native_baseline": BASELINE_IMAGE})
        private_write(self.root/"proxy-mount-before.json", before)
        with os.fdopen(os.open(STATIC_MAIN, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w") as handle:
            handle.write(changed_main)
            handle.flush()
            os.fsync(handle.fileno())

    def test_canary(self, before: dict, changed_main: str) -> None:
        """Use full inherited mounts/networks but no host ports or live routes."""
        name = "xtai-fable187-proxy-check-" + self._invocation
        nginx = before["nginx_inspect"]
        original_environment = environment_map(nginx.get("Config", {}).get("Env"))
        networks = sorted(nginx["NetworkSettings"]["Networks"])
        require(bool(networks), "nginx canary network unavailable")
        command = ["docker", "create", "--name", name, "--label", "xtai.issue187.proxy-check=" + self._invocation,
                   "--entrypoint", "sh", "--network", networks[0]]
        for entry in nginx["Config"].get("Env", []):
            command += ["--env", entry]
        for row in nginx["Mounts"]:
            source, target = row["Source"], row["Destination"]
            if target == FILE_TARGET:
                source, target = str(CONFIG_DIR), DIRECTORY_TARGET
            require(row["Type"] in {"bind", "volume"}, "canary unsupported source mount")
            source = source if row["Type"] == "bind" else row.get("Name")
            require(isinstance(source, str) and source, "canary source volume missing")
            permission = "readonly" if row.get("RW") is False else ""
            mount = "type=" + row["Type"] + ",source=" + source + ",target=" + target
            if permission:
                mount += ",readonly"
            if row["Type"] == "bind":
                require(row.get("Propagation") in {"rprivate", "private"}, "canary unexpected bind propagation")
                mount += ",bind-propagation=" + row["Propagation"]
            command += ["--mount", mount]
        command += ["--mount", "type=bind,source=" + str(STATIC_MAIN) + ",target=" + MAIN_TARGET + ",readonly"]
        host = nginx.get("HostConfig", {})
        for entry in host.get("ExtraHosts") or []:
            command += ["--add-host", entry]
        for entry in host.get("Dns") or []:
            command += ["--dns", entry]
        for entry in host.get("DnsSearch") or []:
            command += ["--dns-search", entry]
        if host.get("ReadonlyRootfs") is True:
            command += ["--read-only"]
        if nginx["Config"].get("WorkingDir"):
            command += ["--workdir", nginx["Config"]["WorkingDir"]]
        if nginx["Config"].get("User"):
            command += ["--user", nginx["Config"]["User"]]
        command += [nginx["Image"], "-c", "sleep 180"]
        created = None
        try:
            created_output = self.run(command).strip()
            require(re.fullmatch(r"[0-9a-f]{64}", created_output) is not None, "private proxy canary ID unavailable")
            created = created_output
            for network in networks[1:]:
                self.run(["docker", "network", "connect", network, created])
            self.run(["docker", "start", created])
            inspect = self.h.inspect(created)
            require(inspect["Image"] == nginx["Image"] and not inspect["HostConfig"].get("PortBindings") and
                    set(inspect["NetworkSettings"]["Networks"]) == set(networks), "canary isolation/image/networks failed")
            require(environment_map(inspect.get("Config", {}).get("Env")) == original_environment, "canary environment names or values changed")
            require(mount_signature(inspect) == replacement_mounts(nginx, directory=True), "canary mounts or RO permissions changed")
            for key in ["ExtraHosts", "Dns", "DnsSearch"]:
                require(optional_host_list(inspect.get("HostConfig", {}).get(key)) == optional_host_list(nginx.get("HostConfig", {}).get(key)),
                        "canary DNS or filesystem settings changed")
            require(inspect.get("HostConfig", {}).get("ReadonlyRootfs") == nginx.get("HostConfig", {}).get("ReadonlyRootfs"),
                    "canary DNS or filesystem settings changed")
            self.run(["docker", "exec", created, "nginx", "-t"])
            require(self.run(["docker", "exec", created, "cat", MAIN_TARGET]) == changed_main, "canary main config mismatch")
            require(self.run(["docker", "exec", created, "cat", FILE_TARGET]) == before["default"], "canary default config mismatch")
            loaded = self.run(["docker", "exec", created, "nginx", "-T"])
            check_loaded_configuration(before["loaded"], loaded)
            private_write(self.root/"proxy-mount-canary-verified.json", {"nginx_image": nginx["Image"], "no_host_ports": True,
                          "all_networks": True, "configuration_exact": True, "at": int(time.time())})
        finally:
            if created:
                inspect = self.h.inspect(created)
                require(inspect.get("Config", {}).get("Labels", {}).get("xtai.issue187.proxy-check") == self._invocation,
                        "canary ownership changed; refuse cleanup")
                self.run(["docker", "rm", "-f", created])

    def create_drain(self) -> None:
        """Exclusively close new image submissions and retain its inode identity."""
        descriptor = os.open(DRAIN, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        identity = os.fstat(descriptor)
        self._drain_identity = (identity.st_dev, identity.st_ino)
        with os.fdopen(descriptor, "w") as handle:
            handle.write(DRAIN_OWNER)
            handle.flush()
            os.fsync(handle.fileno())
        self._drain_complete = True

    def remove_drain(self) -> None:
        """Release only this invocation's exact, unchanged image drain file."""
        identity = DRAIN.stat()
        require(not DRAIN.is_symlink() and (identity.st_dev, identity.st_ino) == self._drain_identity, "image drain inode ownership changed")
        content = DRAIN.read_text()
        require(content == DRAIN_OWNER if self._drain_complete else DRAIN_OWNER.startswith(content), "image drain ownership changed")
        DRAIN.unlink()

    def wait_images_quiet(self) -> bool:
        """Require three quiet image-job samples within a strict 60-second cap."""
        deadline, quiet = time.monotonic()+60, 0
        while time.monotonic() < deadline:
            with sqlite3.connect("file:" + str(GATEWAY_ROOT/"data/image-jobs.sqlite3") + "?mode=ro", uri=True, timeout=2) as connection:
                active = connection.execute("SELECT count(*) FROM image_jobs WHERE status IN ('queued','submitting','running')").fetchone()[0]
                unknown = connection.execute("SELECT count(*) FROM image_jobs WHERE status IS NULL OR status NOT IN ('queued','submitting','running','succeeded','failed','uncertain')").fetchone()[0]
            require(unknown == 0, "unknown image state prohibits proxy recreation")
            quiet = quiet+1 if active == 0 else 0
            if quiet == 3:
                return True
            time.sleep(1)
        return False

    def assert_live_preconditions(self, before: dict) -> None:
        """Reject concurrent native/gateway/proxy edits immediately before writes."""
        require(service_identity(self.h.inspect(NATIVE)) == before["native_identity"] and
                service_identity(self.h.inspect(GATEWAY)) == before["gateway_identity"], "unrelated service changed during maintenance")
        require(self.h.inspect(NGINX)["Id"] == before["nginx_inspect"]["Id"], "nginx changed concurrently")
        require(DEFAULT.read_text() == before["default"] and self.run(["docker", "exec", NGINX, "cat", FILE_TARGET]) == before["default"],
                "live default config changed concurrently")

    def write_compose(self, values: tuple) -> None:
        """Atomically replace only changed fixed-stack files with edit guards."""
        for index, path in enumerate([BASE, OVERRIDE]):
            if values[index] != self._compose_expected[index]:
                self.runtime.atomic_text(path, values[index], self._compose_expected[index])
                updated = list(self._compose_expected)
                updated[index] = values[index]
                self._compose_expected = tuple(updated)

    def stop_proxy(self, before: dict) -> bool:
        """SIGQUIT drains accepted connections; timeout never forces SIGKILL."""
        current = self.h.inspect(NGINX)
        require(current["Id"] == before["nginx_inspect"]["Id"] and
                current.get("HostConfig", {}).get("RestartPolicy") == {"Name": "no", "MaximumRetryCount": 0},
                "graceful nginx restart suppression not verified")
        self.run(["docker", "kill", "--signal", "SIGQUIT", before["nginx_inspect"]["Id"]])
        deadline = time.monotonic()+60
        while time.monotonic() < deadline:
            info = self.h.inspect(NGINX)
            require(info["Id"] == before["nginx_inspect"]["Id"], "graceful nginx identity changed")
            require(info.get("State", {}).get("StartedAt") == before["nginx_inspect"].get("State", {}).get("StartedAt"),
                    "nginx restarted during graceful shutdown; reconcile without recreation")
            if not info.get("State", {}).get("Running"):
                require(info["State"].get("ExitCode") == 0 and info["State"].get("OOMKilled") is False, "nginx did not exit gracefully")
                return True
            time.sleep(1)
        return False

    def disable_restart(self, info: dict) -> None:
        """Temporarily suppress only this exact proxy's daemon restart policy."""
        current = self.h.inspect(NGINX)
        require(current["Id"] == info["Id"] and current.get("State", {}).get("StartedAt") == info.get("State", {}).get("StartedAt") and
                current.get("HostConfig", {}).get("RestartPolicy") == info.get("HostConfig", {}).get("RestartPolicy"),
                "proxy changed before temporary restart suppression")
        self.run(["docker", "update", "--restart=no", info["Id"]])
        updated = self.h.inspect(NGINX)
        require(updated["Id"] == info["Id"] and
                updated.get("HostConfig", {}).get("RestartPolicy") == {"Name": "no", "MaximumRetryCount": 0},
                "temporary nginx restart suppression not confirmed")

    def restore_restart(self, info: dict) -> None:
        """Undo a pre-SIGQUIT policy change only on the still-running same proxy."""
        current = self.h.inspect(NGINX)
        require(current["Id"] == info["Id"] and current.get("State", {}).get("Running") is True and
                current.get("State", {}).get("StartedAt") == info.get("State", {}).get("StartedAt") and
                current.get("HostConfig", {}).get("RestartPolicy") in [info["HostConfig"]["RestartPolicy"], {"Name": "no", "MaximumRetryCount": 0}],
                "cannot restore restart policy on a changed or stopping proxy")
        require(info["HostConfig"]["RestartPolicy"] == {"Name": "unless-stopped", "MaximumRetryCount": 0},
                "original restart policy unsupported")
        self.run(["docker", "update", "--restart=unless-stopped", info["Id"]])
        require(self.h.inspect(NGINX).get("HostConfig", {}).get("RestartPolicy") == info["HostConfig"]["RestartPolicy"],
                "original proxy restart policy restoration not confirmed")

    def compose_up(self) -> None:
        """Recreate the sole authorized nginx service, never its dependencies."""
        self.run(["docker", "compose", "-f", str(BASE), "-f", str(OVERRIDE), "up", "-d", "--no-deps", "--pull", "never", "nginx"], timeout=75)

    def ready(self, before: dict, changed_main: str, *, directory: bool) -> None:
        """Verify proxy settings/routes/site and untouched native/gateway identity."""
        deadline = time.monotonic()+45
        while time.monotonic() < deadline:
            try:
                check_recreated_proxy(before["nginx_inspect"], self.h.inspect(NGINX), directory=directory)
                self.run(["docker", "exec", NGINX, "nginx", "-t"])
                require(self.run(["docker", "exec", NGINX, "cat", FILE_TARGET]) == before["default"], "restored default config mismatch")
                require(self.run(["docker", "exec", NGINX, "cat", MAIN_TARGET]) == (changed_main if directory else before["main"]),
                        "restored main config mismatch")
                loaded = self.run(["docker", "exec", NGINX, "nginx", "-T"])
                if directory:
                    check_loaded_configuration(before["loaded"], loaded)
                else:
                    require(loaded == before["loaded"], "original loaded nginx configuration not restored")
                require(service_identity(self.h.inspect(NATIVE)) == before["native_identity"] and
                        service_identity(self.h.inspect(GATEWAY)) == before["gateway_identity"], "unrelated service was changed")
                with urllib.request.urlopen("https://api.aixingtuyun.com/api/status", timeout=3) as response:
                    require(response.status == 200 and json.load(response).get("success") is True, "public site readiness failed")
                return
            except Exception:
                time.sleep(1)
        raise ProxyMaintenanceError("proxy readiness could not be verified")

    def maintain(self) -> None:
        """Execute the reviewed mount repair or retain guarded recovery evidence."""
        before, changed, changed_main = self.preflight()
        self.backup(before, changed_main)
        self.test_canary(before, changed_main)
        drained = changed_compose = restart_attempted = quit_sent = stopped = recreated = committed = False
        try:
            self.create_drain()
            drained = True
            require(self.wait_images_quiet(), "active image jobs did not drain; nginx untouched")
            self.assert_live_preconditions(before)
            changed_compose = True  # An earlier file may commit before a later failure.
            self.write_compose(changed)
            check_resolved_compose(before["resolved_compose"], self.resolve_compose())
            restart_attempted = True  # Docker update may commit even if its CLI result is lost.
            self.disable_restart(before["nginx_inspect"])
            quit_sent = True  # Signal delivery may be ambiguous if docker CLI times out.
            stopped = self.stop_proxy(before)
            require(stopped, "nginx graceful shutdown pending; no forced stop or recreation")
            recreated = True  # compose may start serving even if its CLI fails.
            self.compose_up()
            self.ready(before, changed_main, directory=True)
            committed = True  # Once ready, accepted traffic prohibits an audit-triggered restart.
            private_write(self.root/"proxy-mount-ready.json", {"at": int(time.time()), "directory_ro": True, "main_include_pinned": True})
            self.remove_drain()
            drained = False
            private_write(self.root/"proxy-mount-verified.json", {"at": int(time.time()), "nginx_image": before["nginx_inspect"]["Image"],
                          "directory_ro": True, "main_include_pinned": True, "loaded_configuration_exact": True,
                          "dormant_configs_unloaded": True, "native_gateway_unchanged": True, "public_status_http": 200})
        except Exception as primary:
            drained = drained or self._drain_identity is not None
            if committed or (quit_sent and not stopped):
                private_write(self.root/"proxy-mount-recovery-required.json", {"at": int(time.time()), "error_type": type(primary).__name__,
                              "ready_proxy_retained": committed, "graceful_shutdown_pending": quit_sent and not stopped,
                              "owned_drain_retained": drained, "no_automatic_replay": True})
                raise ProxyMaintenanceError("proxy phase needs reconciliation; no forced shutdown or replay performed") from None
            try:
                if recreated:
                    current = self.h.inspect(NGINX)
                    if current.get("State", {}).get("Running") is True:
                        check_recreated_proxy(before["nginx_inspect"], current, directory=True)
                        self.disable_restart(current)
                        require(self.stop_proxy({"nginx_inspect": current}),
                                "replacement proxy graceful recovery shutdown pending")
                elif restart_attempted and not quit_sent:
                    self.restore_restart(before["nginx_inspect"])
                if changed_compose:
                    self.write_compose((before["base_compose"], before["override_compose"]))
                if stopped:
                    self.compose_up()
                    self.ready(before, before["main"], directory=False)
                if drained:
                    self.remove_drain()
                private_write(self.root/"proxy-mount-aborted.json", {"at": int(time.time()), "error_type": type(primary).__name__,
                              "original_proxy_restored": stopped, "production_data_unchanged": True})
            except Exception as recovery:
                private_write(self.root/"proxy-mount-recovery-required.json", {"at": int(time.time()), "error_type": type(recovery).__name__,
                              "owned_drain_retained": drained, "no_automatic_replay": True})
                raise ProxyMaintenanceError("proxy recovery incomplete; inspect private evidence before further action") from None
            raise ProxyMaintenanceError("proxy maintenance aborted; no production data rollback") from None


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=["maintain"])
    parser.parse_args()
    try:
        ProxyMountMaintenance().maintain()
        print(json.dumps({"completed": True, "proxy_only": True, "production_data_unchanged": True}))
    except Exception as error:
        print(json.dumps({"completed": False, "error_type": type(error).__name__,
                          "reason": str(error) if isinstance(error, ProxyMaintenanceError) else "inspect private proxy evidence"}))
        raise SystemExit(1) from None
