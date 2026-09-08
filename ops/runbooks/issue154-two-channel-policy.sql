\set ON_ERROR_STOP on

BEGIN;

DO $$
BEGIN
  IF (SELECT COUNT(*) FROM channels WHERE id IN (6, 23, 37, 38, 39, 44, 48, 49, 52)) <> 9 THEN
    RAISE EXCEPTION 'expected channel inventory is incomplete';
  END IF;
  IF COALESCE((SELECT (value::jsonb ->> '文')::numeric FROM options WHERE key = 'GroupRatio'), 0) <> 0.15 THEN
    RAISE EXCEPTION 'text group ratio must remain 0.15';
  END IF;
END $$;

UPDATE channels
SET models = CASE
      WHEN 'gpt-6-astra' = ANY(string_to_array(models, ',')) THEN models
      WHEN COALESCE(models, '') = '' THEN 'gpt-6-astra'
      ELSE models || ',gpt-6-astra'
    END,
    remark = '文字主渠道；GPT-5.5/5.6/GPT-6；海纳账户余额人工确认220元；实际扣费采集恢复前价格只允许保持或上调'
WHERE id = 6;

UPDATE channels
SET remark = '文字第二渠道；寒鹤共享并发上限10；中转站最多使用8并发'
WHERE id = 48;

UPDATE channels
SET remark = 'GPT-Image-2主渠道；近7天响应优于Maolao'
WHERE id = 37;

UPDATE channels
SET remark = 'GPT-Image-2第二渠道；实际成本约0.0618元/张；成功率高但响应较慢'
WHERE id = 23;

UPDATE channels
SET status = 2,
    remark = CASE id
      WHEN 38 THEN '双渠道策略停用：余额耗尽且近24小时GPT-Image-2错误率100%'
      WHEN 39 THEN '双渠道策略停用：余额耗尽且近24小时GPT-5.5错误率94.1%'
      WHEN 49 THEN '双渠道策略停用：GPT-Image-2仅保留海纳与Maolao'
      WHEN 52 THEN '双渠道策略停用：近24小时GPT-Image-2错误率69.8%'
    END
WHERE id IN (38, 39, 49, 52);

UPDATE abilities
SET enabled = FALSE
WHERE channel_id IN (38, 39, 49, 52);

INSERT INTO abilities ("group", model, channel_id, enabled, priority, weight, tag)
SELECT c."group", 'gpt-6-astra', c.id, TRUE, c.priority, c.weight, c.tag
FROM channels c
WHERE c.id = 6
  AND NOT EXISTS (
    SELECT 1
    FROM abilities a
    WHERE a.channel_id = c.id
      AND a.model = 'gpt-6-astra'
      AND a."group" = c."group"
  );

UPDATE abilities
SET enabled = TRUE,
    priority = (SELECT priority FROM channels WHERE id = 6),
    weight = (SELECT weight FROM channels WHERE id = 6)
WHERE channel_id = 6
  AND model = 'gpt-6-astra';

UPDATE options
SET value = value::jsonb || jsonb_build_object(
  'gpt-5.5', 5,
  'gpt-5.6-sol', 5,
  'gpt-5.6-terra', 10,
  'gpt-5.6-luna', 5,
  'gpt-6-astra', 10
)
WHERE key = 'ModelRatio';

UPDATE options
SET value = value::jsonb || jsonb_build_object(
  'gpt-5.5', 6,
  'gpt-5.6-sol', 6,
  'gpt-5.6-terra', 6,
  'gpt-5.6-luna', 6,
  'gpt-6-astra', 5
)
WHERE key = 'CompletionRatio';

UPDATE options
SET value = value::jsonb || jsonb_build_object(
  'gpt-5.5', 0.1,
  'gpt-5.6-sol', 0.1,
  'gpt-5.6-terra', 0.1,
  'gpt-5.6-luna', 0.1,
  'gpt-6-astra', 0.1
)
WHERE key = 'CacheRatio';

DO $$
DECLARE
  over_limit integer;
BEGIN
  SELECT COUNT(*) INTO over_limit
  FROM (
    SELECT a.model
    FROM abilities a
    JOIN channels c ON c.id = a.channel_id
    WHERE a.enabled IS TRUE
      AND c.status = 1
      AND a.model IN (
        'gpt-5.5', 'gpt-5.6-sol', 'gpt-5.6-terra', 'gpt-5.6-luna',
        'gpt-6-astra', 'gpt-image-2', 'banana-flash', 'banana-pro'
      )
    GROUP BY a.model
    HAVING COUNT(DISTINCT a.channel_id) > 2
  ) excessive;
  IF over_limit <> 0 THEN
    RAISE EXCEPTION 'one or more models still have more than two enabled channels';
  END IF;
END $$;

COMMIT;
