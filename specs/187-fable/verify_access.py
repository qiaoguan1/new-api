"""Read-only Fable postdeployment audit. Never submits generation or SQL writes.

Server usage: python3 verify_access.py
Optional complete old-config snapshot: --preservation-before FILE (private
basename only). GET probes use currently valid existing keys, never create or
modify one. Catalog visibility is independently backed by route/policy audits.
"""
import argparse
from collections import Counter
from decimal import Decimal
import importlib.util
import ipaddress
import json
from pathlib import Path
import secrets
import time
import urllib.error
import urllib.request

MODEL = "claude-fable-5-1"
ROOT = Path("/opt/ai-api-stack/backups/fable187-20261007")
PUBLIC = "https://api.aixingtuyun.com"
SERVER_IP = "156.239.3.210"
RATIO_KEYS = ("ModelRatio", "CompletionRatio", "CacheRatio", "CreateCacheRatio")
MODE_KEY, EXPR_KEY = "billing_setting.billing_mode", "billing_setting.billing_expr"
PRICE_FIELDS = {"ModelRatio": "model_ratio", "CompletionRatio": "completion_ratio", "CacheRatio": "cache_ratio",
                "CreateCacheRatio": "create_cache_ratio", MODE_KEY: "billing_mode", EXPR_KEY: "billing_expr"}
TOKEN_SELECT = 'SELECT t.id,t.user_id,t.status,t.expired_time,t.remain_quota,t.unlimited_quota,t.deleted_at,t.key,t.allow_ips,t.model_limits_enabled,t.model_limits,t."group" AS token_group,u.username,u.status AS user_status,u.role AS user_role,u.quota AS user_quota,u."group" AS user_group,u.deleted_at AS user_deleted_at FROM tokens t LEFT JOIN users u ON u.id=t.user_id'


class AccessError(RuntimeError):
    """Safe diagnostic text with no credential or raw server response."""


def require(condition: bool, reason: str) -> None:
    if not condition:
        raise AccessError(reason)


def valid_token(row: dict, now: int) -> bool:
    """Mirror TokenAuth's base validity; wallet funding is not a catalog gate."""
    expiry, quota = row.get("expired_time"), row.get("remain_quota")
    expiry_valid = type(expiry) is int and (expiry == -1 or expiry >= now)
    quota_valid = row.get("unlimited_quota") is True or type(quota) is int and quota > 0
    return (row.get("deleted_at") is None and row.get("user_deleted_at") is None and row.get("status") == 1 and
            row.get("user_status") == 1 and expiry_valid and quota_valid)


def user_usable_groups(user_group: str, policy: dict) -> set:
    """Apply the exact per-user +/- special policy and implicit own user group."""
    usable = set(policy["usable_groups"])
    for name in policy["special_groups"].get(user_group, {}):
        if name.startswith("-:"):
            usable.discard(name[2:])
        else:
            usable.add(name[2:] if name.startswith("+:") else name)
    if user_group:
        usable.add(user_group)
    return usable


def validate_policy(policy: dict) -> None:
    """Require explicit text auto-discovery and well-formed native group maps."""
    require(isinstance(policy.get("auto_groups"), list) and
            all(isinstance(name, str) for name in policy["auto_groups"]) and "文" in policy["auto_groups"],
            "runtime automatic group policy does not include text")
    for name in ("usable_groups", "group_ratios", "special_groups"):
        require(isinstance(policy.get(name), dict), "runtime group policy has invalid shape")
    require(all(isinstance(group, str) and isinstance(overrides, dict) and
                all(isinstance(name, str) for name in overrides)
                for group, overrides in policy["special_groups"].items()),
            "runtime per-user group policy has invalid shape")


def access_reason(row: dict, policy: dict) -> str | None:
    """Require existing auto/text scope and an exact Fable model allowlist."""
    group = row.get("token_group")
    if group not in ("auto", "文"):
        return "unexpected_token_group"
    if row.get("model_limits_enabled") and MODEL not in (row.get("model_limits") or "").split(","):
        return "model_allowlist_blocks_fable"
    if ip_limits(row.get("allow_ips")) and not parsed_ip_limits(row.get("allow_ips")):
        return "invalid_ip_allowlist"
    usable = user_usable_groups(row.get("user_group") or "", policy)
    if group not in usable:
        return "token_group_not_user_usable"
    if group == "auto" and "文" not in [name for name in policy["auto_groups"] if name in usable]:
        return "auto_policy_does_not_include_text"
    if "文" not in policy["group_ratios"]:
        return "text_group_ratio_missing"
    return None


def audit_tokens(rows: list, policy: dict, now: int) -> dict:
    """Audit all base-valid keys before group filtering; do not hide outliers."""
    valid = [row for row in rows if valid_token(row, now)]
    failures = [{"token_id": row["id"], "user_id": row["user_id"], "group": row.get("token_group"), "reason": reason}
                for row in valid if (reason := access_reason(row, policy)) is not None]
    return {"valid_tokens": len(valid), "eligible_tokens": len(valid) - len(failures),
            "groups": dict(Counter(row.get("token_group") for row in valid)), "failures": failures,
            "model_allowlist_enabled": sum(bool(row.get("model_limits_enabled")) for row in valid),
            "ip_restricted": sum(bool(ip_limits(row.get("allow_ips"))) for row in valid),
            "lian_valid_keys": sum(row.get("username") == "lian123" for row in valid)}


def ip_limits(raw: str | None) -> list:
    """Match Token.GetIpLimits: strip spaces, split lines, remove commas."""
    return [part for line in (raw or "").replace(" ", "").split("\n") if (part := line.strip().replace(",", ""))]


def ip_allows(raw: str | None, source: str) -> bool:
    limits = ip_limits(raw)
    if not limits:
        return True
    networks = parsed_ip_limits(raw)
    require(bool(networks), "invalid existing IP allowlist; do not bypass it")
    address = ipaddress.ip_address(source)
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        address = address.ipv4_mapped
    return any(address in network for network in networks)


def parsed_ip_limits(raw: str | None) -> list:
    """Go skips invalid entries, but an entirely malformed ACL permits no IP."""
    networks = []
    for limit in ip_limits(raw):
        try:
            if "%" in limit:
                continue  # Go ParseIP/ParseCIDR does not accept zone identifiers.
            if "/" in limit:
                if limit.count("/") != 1:
                    continue
                _, prefix = limit.split("/")
                if not prefix or any(char < "0" or char > "9" for char in prefix):
                    continue  # Python-only dotted masks are not Go CIDR syntax.
                network = ipaddress.ip_network(limit, strict=False)
                if isinstance(network, ipaddress.IPv6Network) and network.prefixlen >= 96 and network.network_address.ipv4_mapped is not None:
                    network = ipaddress.ip_network(str(network.network_address.ipv4_mapped) + "/" + str(network.prefixlen - 96))
            else:
                address = ipaddress.ip_address(limit)
                if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
                    address = address.ipv4_mapped
                network = ipaddress.ip_network(str(address) + "/" + str(address.max_prefixlen))
            networks.append(network)
        except ValueError:
            continue
    return networks


def same_price(actual, expected) -> bool:
    if isinstance(expected, str):
        return actual == expected
    try:
        return abs(Decimal(str(actual)) - Decimal(str(expected))) <= Decimal("0.0000000001")
    except Exception:
        return False


def verify_pricing_row(row: dict, updates: dict) -> None:
    require(row.get("model_name") == MODEL and "文" in row.get("enable_groups", []), "public Fable text pricing not advertised")
    require(all(same_price(row.get(PRICE_FIELDS[key]), value) for key, value in updates.items()), "public pricing differs from exact True plan")


def compare_option_delta(before: dict, current: dict) -> list:
    """Drop only the exact new Fable entry; report unrelated option drift."""
    changed = []
    for key, previous in before.items():
        if key not in current:
            changed.append(key)
            continue
        try:
            old = json.loads(previous)
        except (ValueError, TypeError):
            old = previous
        try:
            live = json.loads(current[key])
        except (ValueError, TypeError):
            live = current[key]
        if key in PRICE_FIELDS and isinstance(old, dict) and isinstance(live, dict):
            old, live = dict(old), dict(live)
            old.pop(MODEL, None)
            live.pop(MODEL, None)
        if old != live:
            changed.append(key)
    for key in set(current) - set(before):
        if key in [MODE_KEY, EXPR_KEY]:
            extra = dict(json.loads(current[key]))
            extra.pop(MODEL, None)
            if extra:
                changed.append(key)
        else:
            changed.append(key)
    return sorted(set(changed))


def old_metadata_delta(before: list, current: list) -> list:
    """Check every captured field of old metadata, ignoring only exact Fable."""
    expected = [row for row in before if row.get("model_name") != MODEL]
    live = {row["id"]: row for row in current}
    return [row["id"] for row in expected if row["id"] not in live or
            any(live[row["id"]].get(key) != value for key, value in row.items())]


class NoRedirect(urllib.request.HTTPRedirectHandler):
    """Never send an existing credential to a redirect destination."""
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class AccessVerifier:
    def __init__(self, root: Path = ROOT):
        self.root = Path(root)
        self._rollout = None
        self._module = None
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())

    @property
    def rollout(self):
        if self._rollout is None:
            spec = importlib.util.spec_from_file_location("access_rollout", Path(__file__).with_name("rollout.py"))
            require(spec is not None and spec.loader is not None, "rollout helper missing")
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            self._module = module
            self._rollout = module.Rollout(root=self.root)
        return self._rollout

    def get(self, url: str, headers: dict | None = None) -> tuple:
        """GET only; do not print/save raw headers, responses or HTTP exceptions."""
        request = urllib.request.Request(url, headers=headers or {}, method="GET")
        try:
            with self.opener.open(request, timeout=15) as response:
                return response.status, json.loads(response.read(2 * 1024 * 1024))
        except urllib.error.HTTPError as error:
            return error.code, {}
        except Exception:
            return 0, {}

    def catalog(self, token: dict, policy: dict) -> dict:
        """Recheck validity immediately before GET, avoiding invalid-token writes."""
        rows = self.rollout.rows(TOKEN_SELECT + " WHERE t.id=" + str(int(token["id"])))
        require(len(rows) == 1 and valid_token(rows[0], int(time.time())), "existing probe key no longer valid; do not request it")
        current = rows[0]
        require(access_reason(current, policy) is None, "existing key access policy changed or blocks Fable")
        require(current["expired_time"] == -1 or current["expired_time"] >= int(time.time()) + 30,
                "existing key expires too soon for a read-only GET")
        require(ip_allows(current.get("allow_ips"), SERVER_IP), "existing key IP restriction excludes verification host")
        raw = current.get("key")
        require(isinstance(raw, str) and bool(raw), "existing key unavailable")
        bearer = raw if raw.startswith("sk-") else "sk-" + raw
        status, body = self.get(PUBLIC + "/v1/models", {"Authorization": "Bearer " + bearer})
        present = any(item.get("id") == MODEL for item in body.get("data", []) if isinstance(item, dict))
        return {"token_id": current["id"], "group": current["token_group"], "http": status,
                "fable_present": status == 200 and present, "models": [MODEL] if present else []}

    def verify(self, preservation_before: str = "production-config-before.json") -> dict:
        """Produce sanitized evidence even when a scoped access check fails."""
        report = {"success": False, "model": MODEL, "generation_posts": 0, "sql_writes": 0,
                  "at": int(time.time()), "public_gets": [], "lian_keys_verified": 0}
        try:
            require(Path(preservation_before).name == preservation_before, "preservation snapshot must be a private basename")
            r = self.rollout
            plan = r.load_plan()
            image = r.image()["Image"]
            candidate = r.load_candidate()
            require(image == candidate["image"], "current native binary differs from verified candidate")
            saved = r.read("new-api-stage.json")
            routes, abilities, metadata = r.snapshot_routes()
            expected_priorities = {"Claude187:rolldek-ccmax:" + MODEL: 10, "Claude187:maolao:" + MODEL: 8}
            require(len(routes) == 2 and {row["name"]: row["priority"] for row in routes} == expected_priorities and
                    all(row["type"] == 14 and row["models"] == MODEL and row["group"] == "文" and row["status"] == 1 for row in routes),
                    "exact Fable enabled native routes incomplete")
            module = self._module
            module.validate_routes(routes, abilities, saved["channels"], 1)
            require(len(metadata) == 1 and metadata[0]["status"] == 1, "Fable metadata not enabled")
            runtime_gate = r.runtime_gate(saved)
            report["runtime_gate"] = {"image": runtime_gate["image"],
                                      "verified_option_keys": runtime_gate["verified_option_keys"]}
            updates = module.option_updates(plan)
            db_options = r.options()
            require(all(key in db_options and same_price(json.loads(db_options[key]).get(MODEL), value) for key, value in updates.items()),
                    "database six-dimensional pricing differs from True plan")
            admins = r.rows("SELECT id,trim(access_token) AS token FROM users WHERE id=1 AND role=100 AND status=1 AND deleted_at IS NULL")
            require(len(admins) == 1 and bool(admins[0]["token"]), "existing admin read access unavailable")
            status, body = self.get(r.h.address(r.h.NATIVE, 3000) + "/api/option/",
                                    {"Authorization": "Bearer " + admins[0]["token"], "New-Api-User": "1"})
            require(status == 200 and body.get("success") is True, "runtime option GET unavailable")
            runtime = {row["key"]: row["value"] for row in body["data"]}
            require(all(key in runtime and same_price(json.loads(runtime[key]).get(MODEL), value) for key, value in updates.items()),
                    "runtime six-dimensional pricing differs from True plan")
            policy = {"auto_groups": json.loads(runtime["AutoGroups"]), "usable_groups": json.loads(runtime["UserUsableGroups"]),
                      "group_ratios": json.loads(runtime["GroupRatio"]),
                      "special_groups": json.loads(runtime.get("group_ratio_setting.group_special_usable_group", "{}"))}
            validate_policy(policy)
            require(Decimal(str(policy["group_ratios"]["文"])) == Decimal("0.15"), "runtime text ratio differs from .15")
            rows = r.rows(TOKEN_SELECT + " WHERE t.deleted_at IS NULL ORDER BY t.id")
            now = int(time.time())
            report["token_audit"] = audit_tokens(rows, policy, now)
            valid = [row for row in rows if valid_token(row, now)]
            ordinary = [row for row in valid if row.get("user_role") == 1 and row.get("username") != "lian123" and
                        not row.get("model_limits_enabled") and not ip_limits(row.get("allow_ips")) and access_reason(row, policy) is None]
            require(bool(ordinary), "ordinary no-IP valid key unavailable")
            result = self.catalog(ordinary[0], policy)
            report["public_gets"].append({"kind": "ordinary_models", **result})
            lian = [row for row in valid if row.get("username") == "lian123"]
            require(bool(lian), "lian123 active valid keys unavailable")
            for row in lian:
                try:
                    result = self.catalog(row, policy)
                except AccessError as error:
                    result = {"token_id": row["id"], "group": row.get("token_group"), "http": 0,
                              "fable_present": False, "skipped": True, "reason": str(error), "models": []}
                report["public_gets"].append({"kind": "lian_models", **result})
                report["lian_keys_verified"] += int(result["fable_present"])
            status, body = self.get(PUBLIC + "/api/pricing")
            prices = [row for row in body.get("data", []) if row.get("model_name") == MODEL]
            report["public_gets"].append({"kind": "public_pricing", "http": status, "models": [MODEL] if len(prices) == 1 else []})
            require(status == 200 and body.get("success") is True and len(prices) == 1, "public Fable pricing not advertised")
            verify_pricing_row(prices[0], updates)
            before = r.read("new-api-before.json")
            changed = compare_option_delta(before["options"], db_options)
            report["preservation"] = {"unrelated_price_option_changes": changed, "old_routes": "snapshot_not_available"}
            require(not changed, "unrelated price option changes detected")
            if (self.root / preservation_before).is_file():
                snapshot = r.read(preservation_before)
                ids = [int(row["id"]) for row in snapshot.get("channels", [])]
                current = r.rows("SELECT * FROM channels WHERE id IN (" + ",".join(map(str, ids)) + ")") if ids else []
                mutable = {"used_quota", "test_time", "response_time", "balance", "balance_updated_time"}
                normalize = lambda value: {key: item for key, item in value.items() if key not in mutable}
                require({row["id"]: normalize(row) for row in current} == {row["id"]: normalize(row) for row in snapshot.get("channels", [])},
                        "old route configuration changed")
                report["preservation"]["old_routes"] = {"checked_rows": len(ids), "configuration_unchanged": True}
                if "abilities" in snapshot:
                    abilities_now = r.rows("SELECT * FROM abilities WHERE channel_id IN (" + ",".join(map(str, ids)) + ")") if ids else []
                    order = lambda value: (value["channel_id"], value["model"], value["group"])
                    require(sorted(abilities_now, key=order) == sorted(snapshot["abilities"], key=order), "old ability routing changed")
                    report["preservation"]["old_routes"]["abilities_unchanged"] = True
                model_snapshot = snapshot.get("models", snapshot.get("metadata"))
                if model_snapshot is not None:
                    require(isinstance(model_snapshot, list), "old model metadata snapshot invalid")
                    old_ids = [int(row["id"]) for row in model_snapshot if row.get("model_name") != MODEL]
                    old_now = r.rows("SELECT * FROM models WHERE id IN (" + ",".join(map(str, old_ids)) + ")") if old_ids else []
                    require(not old_metadata_delta(model_snapshot, old_now), "old model metadata changed")
                    report["preservation"]["old_routes"].update(metadata_checked_rows=len(old_ids), metadata_unchanged=True)
                if "options" in snapshot:
                    all_db_options = {row["key"]: row["value"] for row in r.rows("SELECT key,value FROM options")}
                    require(not compare_option_delta(snapshot["options"], all_db_options), "unrelated full option snapshot changed")
            require(not report["token_audit"]["failures"], "existing valid token policy blocks exact Fable; no key was changed")
            require(all(row.get("fable_present") is True for row in report["public_gets"] if row["kind"] != "public_pricing"),
                    "ordinary or lian123 public directory verification incomplete; no key scope was changed")
            report.update(success=True, image=image, runtime_prices_verified=True, public_prices_verified=True)
        except Exception as error:
            report.update(error_type=type(error).__name__, reason=str(error) if isinstance(error, AccessError) else "read-only audit prerequisite failed")
        # Importing the helper is side-effect free; no DB/HTTP action occurs if
        # a prerequisite failed before the lazy helper was first loaded.
        _ = self.rollout
        self._module.private_write(self.root / ("access-verified-" + secrets.token_hex(8) + ".json"), report)
        return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--preservation-before", default="production-config-before.json")
    args = parser.parse_args()
    result = AccessVerifier().verify(args.preservation_before)
    audit = result.get("token_audit", {})
    print(json.dumps({"success": result["success"], "models": [MODEL], "valid_counts": audit.get("groups", {}),
                      "valid_tokens": audit.get("valid_tokens"), "eligible_tokens": audit.get("eligible_tokens"),
                      "lian_keys_verified": result["lian_keys_verified"], "public_get_statuses": [{"kind": row["kind"], "http": row["http"], "fable_present": row.get("fable_present")} for row in result["public_gets"]],
                      "policy_failures": len(audit.get("failures", [])), "reason": result.get("reason"), "generation_posts": 0}, ensure_ascii=False))
    raise SystemExit(0 if result["success"] else 1)
