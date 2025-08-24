# Tgmeng-Trend API 接口文档 (最终完整版)

本文档提供了对 `tgmeng-trend` 项目后端 API 的**完整**使用说明，包含每一个接口的请求和响应参数。

## 1. 基本信息

- **基础 URL**: `https://trendapi.tgmeng.com`
- **认证**: 所有接口均为公开接口，无需认证或验签。

---

## 2. 通用数据结构

为保持一致性，所有接口都遵循统一的响应结构。

### 2.1 通用响应结构 (Outer Shell)

| 字段 | 类型 | 描述 |
| :--- | :--- | :--- |
| `code` | Number | 状态码，`200` 表示成功。 |
| `message` | String | 请求结果的消息，例如 "请求成功"。 |
| `data` | Object | 核心数据对象，详见下文。 |

### 2.2 data 结构

`data` 对象包含了热榜的元数据和具体内容。

| 字段 | 类型 | 描述 |
| :--- | :--- | :--- |
| `dataInfo` | Array | 热榜条目数组，数组中的每个元素都是一个 `dataInfo` 对象。 |
| `dataCardName` | String | 数据卡片名称，例如 "腾讯"、"微博"。 |
| `dataCardLogo` | String | 数据卡片 Logo 的 URL。 |
| `dataCardCategory` | String | 数据卡片分类，例如 "新闻"、"社交"。 |
| `dataSize` | Number | 热榜中的条目总数。 |
| `dataUpdateTime` | String | 数据更新时间，格式为 `YYYY-MM-DD HH:mm:ss`。 |

### 2.3 dataInfo 结构 (热榜条目)

`dataInfo` 数组中的每个对象代表一个热榜条目。

| 字段 | 类型 | 描述 |
| :--- | :--- | :--- |
| `keyword` | String | 热榜关键词或标题。 |
| `hotScore` | String | 热度分值。 |
| `url` | String | 原始新闻或内容的链接。 |
| `image` | String | 缩略图 URL，可能为 `null`。 |
| `author` | String | 作者，可能为 `null`。 |
| `desc` | String | 简要描述，可能为 `null`。 |
| `type` | String | 类型标签，例如微博的 "新"、"热" 等，可能为 `null`。 |
| `publishTime` | String | 发布时间，可能为 `null`。 |

---

## 3. 接口详情

### 3.1 通用热榜

#### 3.1.1 百度热搜
- **请求**: `GET /api/topsearch/baidu`
- **请求参数**: 无

#### 3.1.2 Bilibili
- **请求**: `GET /api/topsearch/bilibili`
- **请求参数**: 无

#### 3.1.3 微博
- **请求**: `GET /api/topsearch/weibo`
- **请求参数**: 无

#### 3.1.4 抖音
- **请求**: `GET /api/topsearch/douyin`
- **请求参数**: 无

#### 3.1.5 豆瓣
- **请求**: `GET /api/topsearch/douban`
- **请求参数**: 无

#### 3.1.6 腾讯新闻
- **请求**: `GET /api/topsearch/tencent`
- **请求参数**: 无

#### 3.1.7 今日头条
- **请求**: `GET /api/topsearch/toutiao`
- **请求参数**: 无

#### 3.1.8 网易新闻
- **请求**: `GET /api/topsearch/wangyi`
- **请求参数**: 无

#### 3.1.9 网易云音乐-飙升榜
- **请求**: `GET /api/topsearch/biaoshengwangyiyun`
- **请求参数**: 无

#### 3.1.10 网易云音乐-新歌榜
- **请求**: `GET /api/topsearch/xingegwangyiyun`
- **请求参数**: 无

#### 3.1.11 网易云音乐-原创榜
- **请求**: `GET /api/topsearch/yuanchuangwangyiyun`
- **请求参数**: 无

#### 3.1.12 网易云音乐-热歌榜
- **请求**: `GET /api/topsearch/regewangyiyun`
- **请求参数**: 无

#### 3.1.13 百度贴吧
- **请求**: `GET /api/topsearch/tiebabaidu`
- **请求参数**: 无

#### 3.1.14 少数派
- **请求**: `GET /api/topsearch/shaoshupai`
- **请求参数**: 无

#### 3.1.15 百度-电视剧
- **请求**: `GET /api/topsearch/dianshijubaidu`
- **请求参数**: 无

#### 3.1.16 百度-小说
- **请求**: `GET /api/topsearch/xiaoshuobaidu`
- **请求参数**: 无

#### 3.1.17 百度-电影
- **请求**: `GET /api/topsearch/dianyingbaidu`
- **请求参数**: 无

#### 3.1.18 百度-游戏
- **请求**: `GET /api/topsearch/youxibaidu`
- **请求参数**: 无

#### 3.1.19 百度-汽车
- **请求**: `GET /api/topsearch/qichebaidu`
- **请求参数**: 无

#### 3.1.20 百度-热梗
- **请求**: `GET /api/topsearch/regengbaidu`
- **请求参数**: 无

#### 3.1.21 百度-财经
- **请求**: `GET /api/topsearch/caijingbaidu`
- **请求参数**: 无

#### 3.1.22 百度-民生
- **请求**: `GET /api/topsearch/minshengbaidu`
- **请求参数**: 无

#### 3.1.23 知乎
- **请求**: `GET /api/topsearch/zhihu`
- **请求参数**: 无

#### 3.1.24 腾讯视频-电视剧
- **请求**: `GET /api/topsearch/dianshijutengxun`
- **请求参数**: 无

#### 3.1.25 腾讯视频-电影
- **请求**: `GET /api/topsearch/dianyingtengxun`
- **请求参数**: 无

#### 3.1.26 腾讯视频-动漫
- **请求**: `GET /api/topsearch/dongmantengxun`
- **请求参数**: 无

#### 3.1.27 腾讯视频-综艺
- **请求**: `GET /api/topsearch/zongyitengxun`
- **请求参数**: 无

#### 3.1.28 腾讯视频-总榜
- **请求**: `GET /api/topsearch/zongbangtengxun`
- **请求参数**: 无

#### 3.1.29 爱奇艺-电视剧
- **请求**: `GET /api/topsearch/dianshijuaiqiyi`
- **请求参数**: 无

#### 3.1.30 爱奇艺-电影
- **请求**: `GET /api/topsearch/dianyingaiqiyi`
- **请求参数**: 无

#### 3.1.31 爱奇艺-动漫
- **请求**: `GET /api/topsearch/dongmanaiqiyi`
- **请求参数**: 无

#### 3.1.32 爱奇艺-综艺
- **请求**: `GET /api/topsearch/zongyiaiqiyi`
- **请求参数**: 无

#### 3.1.33 爱奇艺-总榜
- **请求**: `GET /api/topsearch/zongbangaiqiyi`
- **请求参数**: 无

#### 3.1.34 优酷-电视剧
- **请求**: `GET /api/topsearch/dianshijuyouku`
- **请求参数**: 无

#### 3.1.35 优酷-电影
- **请求**: `GET /api/topsearch/dianyingyouku`
- **请求参数**: 无

#### 3.1.36 优酷-动漫
- **请求**: `GET /api/topsearch/dongmanyouku`
- **请求参数**: 无

#### 3.1.37 优酷-综艺
- **请求**: `GET /api/topsearch/zongyiyouku`
- **请求参数**: 无

#### 3.1.38 优酷-总榜
- **请求**: `GET /api/topsearch/zongbangyouku`
- **请求参数**: 无

#### 3.1.39 芒果TV-电视剧
- **请求**: `GET /api/topsearch/dianshijumangguo`
- **请求参数**: 无

#### 3.1.40 芒果TV-电影
- **请求**: `GET /api/topsearch/dianyingmangguo`
- **请求参数**: 无

#### 3.1.41 芒果TV-动漫
- **请求**: `GET /api/topsearch/dongmanmangguo`
- **请求参数**: 无

#### 3.1.42 芒果TV-综艺
- **请求**: `GET /api/topsearch/zongyimangguo`
- **请求参数**: 无

#### 3.1.43 芒果TV-总榜
- **请求**: `GET /api/topsearch/zongbangmangguo`
- **请求参数**: 无

#### 3.1.44 猫眼-周票房榜
- **请求**: `GET /api/topsearch/zhoupiaofangbangmaoyan`
- **请求参数**: 无

#### 3.1.45 猫眼-想看榜
- **请求**: `GET /api/topsearch/xiangkanbangmaoyan`
- **请求参数**: 无

#### 3.1.46 猫眼-购票评分榜
- **请求**: `GET /api/topsearch/goupiaopingfenbangmaoyan`
- **请求参数**: 无

#### 3.1.47 猫眼-TOP100
- **请求**: `GET /api/topsearch/top100maoyan`
- **请求参数**: 无

---

### 3.2 GitHub 热榜

#### 3.2.1 总榜
- **请求**: `GET /api/topsearch/github/allstars`
- **请求参数**: 无

#### 3.2.2 日榜
- **请求**: `GET /api/topsearch/github/daystars`
- **请求参数**: 无

#### 3.2.3 周榜
- **请求**: `GET /api/topsearch/github/weekstars`
- **请求参数**: 无

#### 3.2.4 月榜
- **请求**: `GET /api/topsearch/github/monthstars`
- **请求参数**: 无

#### 3.2.5 年榜
- **请求**: `GET /api/topsearch/github/yearstars`
- **请求参数**: 无

#### 3.2.6 三年榜
- **请求**: `GET /api/topsearch/github/threeyearstars`
- **请求参数**: 无

#### 3.2.7 五年榜
- **请求**: `GET /api/topsearch/github/fiveyearstars`
- **请求参数**: 无

#### 3.2.8 十年榜
- **请求**: `GET /api/topsearch/github/tenyearstars`
- **请求参数**: 无

---

### 3.3 全球热榜

#### 3.3.1 YouTube
- **请求**: `GET /api/topsearch/global/youtube`
- **请求参数**: 无

#### 3.3.2 Hugging Face Spaces Trending
- **请求**: `GET /api/topsearch/global/huggingfacespacestrending`
- **请求参数**: 无

#### 3.3.3 Hugging Face Spaces Likes
- **请求**: `GET /api/topsearch/global/huggingfacespaceslikes`
- **请求参数**: 无

#### 3.3.4 Hugging Face Models Trending
- **请求**: `GET /api/topsearch/global/huggingfacemodelstrending`
- **请求参数**: 无

#### 3.3.5 Hugging Face Models Likes
- **请求**: `GET /api/topsearch/global/huggingfacemodellikes`
- **请求参数**: 无

#### 3.3.6 Hugging Face Datasets Trending
- **请求**: `GET /api/topsearch/global/huggingfacedatasetstrending`
- **请求参数**: 无

#### 3.3.7 Hugging Face Datasets Likes
- **请求**: `GET /api/topsearch/global/huggingfacedatasetslikes`
- **请求参数**: 无
