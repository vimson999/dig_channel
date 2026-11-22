# 图书带货情报导入 JSON 约定

状态：与当前内部导入器一致，可供数据提供方按此准备文件。本文档用于交付格式，不表示已连接或委托任何外部采集服务。

## 系统入口

系统导航中打开 **选题情报 → 发现机会 → 图书带货情报**。管理员在页面的“数据导入”页签上传 `.json` 文件，先预览，再确认导入；需要生成增长榜和候选池时，选择日期范围并提交计算。

入口只在 `ENABLE_BOOK_COMMERCE=1` 且模块数据库结构就绪时显示。账号/书籍/商品/视频引用和观测写在同一批文件中；页面预览会检查整批数据，任何无效行或关联冲突都会阻止业务数据写入。分析任务由独立 `book_commerce` worker 消费。

## 文件结构和限制

- UTF-8 JSON 对象，文件最大 5 MiB；下例可单独保存为 `.json` 并预览。
- 顶层只接受 `source`、`adapter_version`、`accounts`、`books`、`listings`、`videos`、`account_listings`、`video_listing_links`、`observations`。未知字段会使整批失败。
- `source` 是数据提供方/适配器的稳定名称，最多 100 字符；`adapter_version` 是其数据适配版本，最多 64 字符。
- 每个数组最多 1,000 条；缺少的数组按空数组处理。单条中的额外字段也会被拒绝。
- JSON 文件里不要包含登录令牌、Cookie、用户密码、签名链接或其他凭据。`source_url` 与 `evidence_reference` 只允许不带凭据参数的 HTTP(S) 链接。
- 不传平台字段；这套内部模块固定用于视频号。所有计数值使用非负整数，不提供计算好的 `delta_*`，增量由系统根据不同时点的累计观测推导。

## 身份键与关联引用

账号、书籍、商品和视频使用 `(source, source_key)` 唯一标识。`source_key` 必须在同一来源的多次交付中保持稳定；不能用名称或数组顺序代替。`external_id` 是平台/店铺可见标识，可选但如果提供必须保持一致。

文件内关联字段用目标实体的 `source_key`，而不是数据库 ID：`videos.account` 指向账号；`listings.book` 指向书籍；`account_listings.account/listing` 指向账号与商品；`video_listing_links.video/account/listing` 指向视频、账号与商品；观测行的 `account/listing/video` 也采用相同规则。被引用实体应放在同一文件中或已由相同 `source` 导入。

同名实体不会自动合并。出现稳定键身份冲突、找不到引用或视频与商品不属于同一账号时，整批拒绝；请由数据源修正后用页面重新预览并提交新批次。

## 主数据字段

所有时间为 ISO 8601 且必须带时区，例如 `2026-11-17T20:00:00+08:00` 或 `2026-11-17T12:00:00Z`。`observed_at` 表示该实体信息被采集/观察到的时刻。历史时间可以导入，但不能比服务器当前 UTC 时间晚超过 5 分钟。

| 数组 | 必填字段 | 可选字段 |
|---|---|---|
| `accounts` | `source_key`, `name`, `observed_at` | `external_id`, `categories`（推荐，最多50条赛道路径）, `category`（旧格式兼容）, `bio`, `location`, `is_ai_content`（布尔值）, `source_url` |
| `books` | `source_key`, `title`, `observed_at` | `isbn13`（通过 ISBN-13 校验）, `author`, `publisher`, `edition`, `category` |
| `listings` | `source_key`, `title`, `observed_at` | `external_id`, `shop_external_id`, `shop_name`, `variant`, `book`（书籍 `source_key`）, `source_url` |
| `videos` | `source_key`, `account`, `title`, `observed_at` | `external_id`, `published_at`, `source_url`, `core_hook`, `script_text`, `transcript_status`, `transcript_source`, `transcript_updated_at` |
| `account_listings` | `account`, `listing`, `observed_at` | `status`：`active`、`inactive` 或 `unknown`，缺省为 `unknown` |
| `video_listing_links` | `video`, `account`, `listing`, `valid_from`, `observed_at`, `relation_type`, `verification_status` | `valid_to`, `evidence_reference` |

`video_listing_links.relation_type` 为 `attached`（视频确实挂载该商品）或 `mentioned`（视频仅提及该商品）。`verification_status` 为 `confirmed`、`unverified` 或 `rejected`。只有有明确页面/素材依据的挂载关系才标为 `confirmed`；推荐候选只使用已确认且覆盖观测窗口的挂载关系。`valid_from`/`valid_to` 表示关系生效时间，`valid_to` 可为 `null`，不可晚于 `observed_at`。

账号赛道以 `bc_categories` 和 `bc_account_categories` 关系表为准。新导入优先使用 `categories` 字符串数组，例如 `"categories": ["健康养生 / 慢病预防", "家庭营养"]`；每个字符串是一条层级路径，`/` 或 `／` 分隔父级和子级。旧 `category` 字段仍可传一条路径，系统会拆分入关系表。页面赛道筛选支持关键词搜索和多选，多项按“命中任一项”处理；选择父级包含其所有子级。

## 观测字段和质量

`observations` 的每行包含 `kind`、`account`、`observed_at`、`values` 和 `quality`。`values` 与 `quality` 的键必须完全相同。所有“累计”指标应交付当时采集到的累计值；不要预先求差。

| `kind` | 其他必填引用 | 支持的 `values` 字段 |
|---|---|---|
| `account` | 无 | `total_posts`, `showcase_rating`, `buyers_count`, `total_sales`, `repeat_customers`, `total_products` |
| `showcase` | `listing`; `scope` 可选，缺省 `unknown` | `cumulative_sales`, `price`, `currency`, `availability` |
| `video` | `video` | `likes`, `shares`, `comments`, `is_pinned` |

`showcase.scope` 为 `account_listing`、`platform_listing` 或 `unknown`。只有明确属于某账号橱窗的商品销售快照填 `account_listing`；平台商品页的公开累计值填 `platform_listing`；无法判定时填 `unknown`。平台级或未知口径不会用于账号视频候选归因，也不会跨账号累加。

每个指标的 `quality` 必须是以下一种：

- `exact`：来源明确提供精确值。计数值为 JSON 整数或只含数字的十进制字符串，范围 `0` 到 `9223372036854775807`；布尔值只用于 `is_pinned`；`showcase_rating` 和 `price` 用最多两位小数的字符串（例如 `"4.73"`、`"49.80"`）。`currency` 当前只接受 `CNY`。
- `rounded`：来源只提供约数，`values` 对应值必须是非空文本，例如 `"约1.2万"`。
- `lower_bound`：来源只给出下限/区间描述，`values` 对应值必须是非空文本，例如 `"1万+"`。
- `unknown`：`values` 对应值必须为 `null`；不等同于 0。

`showcase_rating` 取值应在 0–5。对某一日期不确定或未采到的指标不要伪造 0；可省略该指标，或明确使用 `unknown` 与 `null`。只要候选所需的销量或转发缺少精确、可比较的累计基线，系统就不会给出该条件的有效增量。

## 合成示例

文件 [sample-import.json](sample-import.json) 是可用于开发预览的合成数据：同账号、同商品和视频带有两个连续日期的观测，可演示系统如何从累计值推导增量。**示例人物、商品、地址和数值均为虚构，请勿当作真实业务数据导入。**

相同视频/商品/账号在两个连续观测窗口中满足严格转发阈值，只能形成“同期增长线索”，不能证明该视频造成了具体订单。系统也不接受来源提供的因果结论或 `delta_sales`、`delta_shares` 字段。

## 交付前检查清单

1. `source_key` 在后续文件中稳定；各实体引用都能解析。
2. 每个观测都有真实采集时刻及明确时区；累计销量/互动量没有被改写成日增量。
3. 精确、约数、下限和未知值已按 `quality` 如实区分；缺失不是 0。
4. 确认视频与商品关系，并附上可访问且无凭据的证据引用；未确认的关系不要标为 `confirmed`。
5. 文件为 UTF-8 JSON，所有字段严格遵守上述白名单。先由系统预览；预览通过后再确认导入。

提交的 JSON 结构是系统内部已实现的导入 DTO。数据提供方只需交付文件给系统管理员；本约定本身不授权系统直接连接对方平台或自动抓取数据。
