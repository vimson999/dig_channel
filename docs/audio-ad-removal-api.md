# 音频去广告接口

`POST /api/audio-ad-removal/process` 可供项目内其他功能或已登录的客户端调用。请求使用 `multipart/form-data`：

| 字段 | 必填 | 说明 |
| --- | --- | --- |
| `audio` | 是 | 待处理音频，最多 500 MB |
| `sample` | 否 | 广告音频样本，最多 30 MB；省略时使用项目默认样本 |
| `threshold` | 否 | 匹配阈值，0.1–0.99，默认 0.55 |

成功时直接返回 `audio/mpeg` 文件；`X-Ad-Matches` 响应头表示找到的广告段数。未找到匹配段时仍返回 MP3。处理失败返回 JSON `{"error": "..."}`。接口沿用项目的登录鉴权，自动匹配结果会直接剪除；需要逐段试听确认时使用页面原有的上传、查找、导出接口。

```bash
curl -X POST http://127.0.0.1:5001/api/audio-ad-removal/process \
  -b cookies.txt -F 'audio=@lecture.mp3' -F 'sample=@advert.wav' \
  -o lecture-clean.mp3
```
