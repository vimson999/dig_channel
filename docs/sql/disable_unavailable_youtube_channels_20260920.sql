-- DBeaver 人工执行脚本：停用两个已被 YouTube Data API 判定为不存在/不可用的频道。
-- 证据时间：2026-09-20；channels.list(part=snippet,statistics) 对两个 external channel_id
-- 均返回 pageInfo.totalResults=0、items=[]。执行前必须重新调用 API 确认，避免临时平台故障误停。
-- 本脚本默认 ROLLBACK，只用于预演。确认 RETURNING 恰好两行且身份无误后，
-- 将末尾 ROLLBACK 改为 COMMIT 再逐条执行。不要整段无人值守执行。

BEGIN;
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '30s';

-- 1. 前置核对：只允许命中这两个精确业务身份。
SELECT id, channel_id, name, is_active, updated_at
FROM channels
WHERE (id, channel_id, name) IN (
    (21, 'UCWzdWJ9tpBEd2BwMtkbWS-A', 'Smartpet Video AI'),
    (56, 'UCi6kbBNPjb7F-6LcHqUdTiA', '数字指南针')
)
ORDER BY id;

-- 2. 核对最近快照：当前证据分别停留在 2026-09-09 和 2026-06-18。
SELECT c.id, c.channel_id, c.name, MAX(cd.snapshot_date) AS last_snapshot_date
FROM channels AS c
LEFT JOIN channel_data AS cd ON cd.channel_id = c.id
WHERE (c.id, c.channel_id, c.name) IN (
    (21, 'UCWzdWJ9tpBEd2BwMtkbWS-A', 'Smartpet Video AI'),
    (56, 'UCi6kbBNPjb7F-6LcHqUdTiA', '数字指南针')
)
GROUP BY c.id, c.channel_id, c.name
ORDER BY c.id;

-- 3. 受保护变更：精确匹配 id、外部 channel_id、名称与当前启用状态。
UPDATE channels
SET is_active = FALSE,
    updated_at = NOW()
WHERE is_active = TRUE
  AND (id, channel_id, name) IN (
      (21, 'UCWzdWJ9tpBEd2BwMtkbWS-A', 'Smartpet Video AI'),
      (56, 'UCi6kbBNPjb7F-6LcHqUdTiA', '数字指南针')
  )
RETURNING id, channel_id, name, is_active, updated_at;

-- 4. 验证：应返回两行且两行 is_active 均为 false。
SELECT id, channel_id, name, is_active, updated_at
FROM channels
WHERE id IN (21, 56)
ORDER BY id;

-- 安全默认值：预演不落库。人工确认后改为 COMMIT。
ROLLBACK;

-- 已 COMMIT 后的人工回滚（仅在确认需要重新启用且频道身份未变化时单独执行）：
-- BEGIN;
-- SET LOCAL lock_timeout = '5s';
-- SET LOCAL statement_timeout = '30s';
-- UPDATE channels
-- SET is_active = TRUE, updated_at = NOW()
-- WHERE is_active = FALSE
--   AND (id, channel_id, name) IN (
--       (21, 'UCWzdWJ9tpBEd2BwMtkbWS-A', 'Smartpet Video AI'),
--       (56, 'UCi6kbBNPjb7F-6LcHqUdTiA', '数字指南针')
--   )
-- RETURNING id, channel_id, name, is_active, updated_at;
-- COMMIT;
