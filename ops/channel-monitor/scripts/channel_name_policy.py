#!/usr/bin/env python3
"""Canonical operator-visible names for the complete production channel inventory."""


CHANNEL_NAMES = {
    1: ("自有号", "自有 · 综合"),
    2: ("DeepSeek", "DeepSeek · 文字"),
    3: ("千问", "千问 · 综合"),
    4: ("智普", "智谱 · 综合"),
    5: ("doubao文字", "Doubao · 文字"),
    6: ("海纳文", "海纳 · 文字"),
    11: ("doubao视频", "Doubao · 视频"),
    12: ("doubao图片", "Doubao · 图片"),
    13: ("aigocode", "AigoCode · 综合"),
    14: ("aihua", "Aihua · 综合"),
    15: ("packapi 图", "PackAPI · 图片"),
    16: ("apikeyfun", "APIKeyFun · OpenAI"),
    18: ("Runapi", "RUNapi · 综合"),
    19: ("token云桥", "Token 云桥 · 综合"),
    20: ("maolao API 文", "Maolao API · 文字"),
    21: ("unity2.ai 图", "Unity2 · 图片"),
    22: ("unity2.ai 文", "Unity2 · 文字"),
    23: ("maolao API 图", "Maolao API · 图片"),
    27: ("icreat ", "iCreat · 综合"),
    28: ("nodyhub文", "NodyHub · 文字"),
    29: ("packapi 文", "PackAPI · 文字"),
    30: ("jojocode 文", "JojoCode · 文字"),
    31: ("jojocode 文图", "JojoCode · 文字备用"),
    32: ("jojocode 图", "JojoCode · 图片"),
    33: ("runninghub", "RunningHub · 图片"),
    34: ("runninghub-remove-bg-workflow", "RunningHub · 去背景工作流"),
    35: ("apikeyfun", "APIKeyFun · Claude"),
    36: ("openrouter", "OpenRouter · 综合"),
    37: ("海纳图", "海纳 · 图片"),
    38: ("codeplan图", "Code Plan · 图片"),
    39: ("code plan文", "Code Plan · 文字"),
    40: ("香蕉图", "NodyHub · 多媒体精选"),
    41: ("nody视频相关", "NodyHub · 全模型"),
    42: ("paisio 视频", "Paisio · 视频"),
    43: ("tp视频放大", "Topaz · 视频放大"),
}


def validate_policy():
    """Reject ambiguous or non-canonical policy definitions."""
    names = [new for _, new in CHANNEL_NAMES.values()]
    if len(names) != len(set(names)):
        raise ValueError("canonical channel names must be unique")
    for channel_id, (_, name) in CHANNEL_NAMES.items():
        if name.count(" · ") != 1 or name != name.strip():
            raise ValueError(f"channel {channel_id} violates '上游名 · 用途': {name!r}")


def validate_inventory(rows):
    """Return `pending` or `applied`; reject missing, new, or drifted rows."""
    validate_policy()
    actual = {int(row["id"]): str(row.get("name") or "") for row in rows}
    expected_ids = set(CHANNEL_NAMES)
    if set(actual) != expected_ids:
        missing = sorted(expected_ids - set(actual))
        extra = sorted(set(actual) - expected_ids)
        raise ValueError(f"channel inventory drift: missing={missing}, extra={extra}")
    pending = False
    for channel_id, (old, new) in CHANNEL_NAMES.items():
        current = actual[channel_id]
        if current == old:
            pending = True
        elif current != new:
            raise ValueError(
                f"channel {channel_id} name drift: expected {old!r} or {new!r}, got {current!r}"
            )
    return "pending" if pending else "applied"


def sql_literal(value):
    return "'" + str(value).replace("'", "''") + "'"


def migration_sql():
    """Build one guarded transaction that can modify only `channels.name`."""
    validate_policy()
    values = ",\n".join(
        f"({channel_id}, {sql_literal(old)}, {sql_literal(new)})"
        for channel_id, (old, new) in CHANNEL_NAMES.items()
    )
    return f"""BEGIN;
LOCK TABLE channels IN SHARE ROW EXCLUSIVE MODE;
CREATE TEMP TABLE channel_name_policy(id integer, old_name text, new_name text) ON COMMIT DROP;
INSERT INTO channel_name_policy(id, old_name, new_name) VALUES
{values};
DO $policy$
BEGIN
  IF (SELECT count(*) FROM channels) <> {len(CHANNEL_NAMES)} THEN
    RAISE EXCEPTION 'channel inventory changed';
  END IF;
  IF EXISTS (
    SELECT 1 FROM channels c
    FULL JOIN channel_name_policy p USING (id)
    WHERE c.id IS NULL OR p.id IS NULL OR c.name NOT IN (p.old_name, p.new_name)
  ) THEN
    RAISE EXCEPTION 'channel name policy drift detected';
  END IF;
END
$policy$;
CREATE TEMP TABLE channel_name_fingerprint ON COMMIT DROP AS
SELECT md5(jsonb_agg(to_jsonb(c) - 'name' ORDER BY id)::text) AS value FROM channels c;
UPDATE channels c SET name = p.new_name
FROM channel_name_policy p WHERE c.id = p.id AND c.name <> p.new_name;
DO $policy$
BEGIN
  IF EXISTS (
    SELECT 1 FROM channels c JOIN channel_name_policy p USING (id)
    WHERE c.name <> p.new_name
  ) THEN
    RAISE EXCEPTION 'canonical names were not fully applied';
  END IF;
  IF (SELECT value FROM channel_name_fingerprint) <>
     (SELECT md5(jsonb_agg(to_jsonb(c) - 'name' ORDER BY id)::text) FROM channels c) THEN
    RAISE EXCEPTION 'a non-name channel field changed';
  END IF;
END
$policy$;
COMMIT;
"""


validate_policy()
