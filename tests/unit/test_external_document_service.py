import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch


BACKEND_DIR = Path(__file__).resolve().parents[2] / "src" / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from services import external_document_service  # noqa: E402


class ExternalDocumentServiceImportGuardTest(unittest.TestCase):
    def test_missing_feishu_cookie_resolver_fails_lazily(self):
        with patch.object(
            external_document_service.credential_service,
            "resolve_feishu_cookie",
            None,
            create=True,
        ):
            with self.assertRaisesRegex(external_document_service.ExternalDocumentError, "滚动重启 worker"):
                external_document_service._resolve_feishu_cookie(required=True)


class FeishuDocumentResponseValidationTest(unittest.TestCase):
    def test_rejects_docs_template_home_returned_as_http_200(self):
        content = "\n".join([
            "Docs",
            "模板推荐",
            "Recommended templates",
            "Bitable_VibeView_Onboard_BuildProductCatalog_Title",
            "✨ 明星企业案例",
        ])

        with self.assertRaisesRegex(
            external_document_service.ExternalDocumentError,
            "Docs 首页/模板中心",
        ):
            external_document_service._validate_feishu_document_response(
                title="Docs",
                content_text=content,
            )

    def test_rejects_docs_home_when_recommendations_change(self):
        content = "\n".join([
            "Docs",
            "入门推荐",
            "新春定制贺卡",
            "文案一键生成",
            "自动化流程",
            "可视化仪表盘",
        ])

        with self.assertRaisesRegex(
            external_document_service.ExternalDocumentError,
            "Docs 首页/模板中心",
        ):
            external_document_service._validate_feishu_document_response(
                title="Docs",
                content_text=content,
            )

    def test_accepts_real_document_content(self):
        external_document_service._validate_feishu_document_response(
            title="零代码基础，我用 AI 编程 25 天变现 5100 元",
            content_text="这是完整复盘正文，包含需求确认、开发、验收、交付和复盘步骤。" * 20,
        )


class FeishuDynamicDocumentFetchTest(unittest.TestCase):
    def test_docs_shell_falls_back_to_browser_rendered_content(self):
        response = SimpleNamespace(
            status_code=200,
            encoding="utf-8",
            text="<html><head><title>Docs</title></head><body>模板推荐</body></html>",
            url="https://my.feishu.cn/wiki/example",
            headers={"content-type": "text/html; charset=utf-8"},
        )
        session = Mock()
        session.get.return_value = response
        rendered = {
            "title": "真实飞书文档",
            "content_text": "这是浏览器渲染后抽取的真实正文。" * 20,
            "content_html": None,
            "raw_payload": {"extractor": "browser_dom_v1", "rendered_blocks": 12},
        }
        document = SimpleNamespace(
            canonical_url="https://my.feishu.cn/wiki/example",
            title=None,
        )

        with patch.object(external_document_service.requests, "Session", return_value=session), patch.object(
            external_document_service,
            "_fetch_feishu_document_with_browser",
            return_value=rendered,
        ) as browser_fetch:
            result = external_document_service.ExternalDocumentService()._fetch_feishu_document(
                document,
                "session=valid",
            )

        browser_fetch.assert_called_once_with(document.canonical_url, "session=valid")
        self.assertEqual(result["title"], "真实飞书文档")
        self.assertEqual(result["raw_payload"]["extractor"], "browser_dom_v1")
        self.assertEqual(result["raw_payload"]["initial_title"], "Docs")

    def test_static_real_document_does_not_launch_browser(self):
        response = SimpleNamespace(
            status_code=200,
            encoding="utf-8",
            text=(
                "<html><head><title>真实飞书文档</title></head><body>"
                + "这是服务端直接返回的完整正文。" * 30
                + "</body></html>"
            ),
            url="https://example.feishu.cn/docx/example",
            headers={"content-type": "text/html; charset=utf-8"},
        )
        session = Mock()
        session.get.return_value = response
        document = SimpleNamespace(
            canonical_url="https://example.feishu.cn/docx/example",
            title=None,
        )

        with patch.object(external_document_service.requests, "Session", return_value=session), patch.object(
            external_document_service,
            "_fetch_feishu_document_with_browser",
        ) as browser_fetch:
            result = external_document_service.ExternalDocumentService()._fetch_feishu_document(
                document,
                "session=valid",
            )

        browser_fetch.assert_not_called()
        self.assertEqual(result["raw_payload"]["extractor"], "html_text_v1")


class ExternalLinkClassificationTest(unittest.TestCase):
    def test_zsxq_topic_links_are_registered_as_item_relations(self):
        self.assertEqual(
            external_document_service.provider_for_url(
                "https://wx.zsxq.com/dweb2/index/topic_detail/123456789"
            ),
            "zsxq",
        )
        self.assertEqual(
            external_document_service.topic_id_for_zsxq_url(
                "https://wx.zsxq.com/dweb2/index/topic_detail/123456789"
            ),
            "123456789",
        )

    def test_mixed_topic_keeps_feishu_and_zsxq_relations(self):
        links = external_document_service.extract_external_links_from_topic({
            "content": (
                "原帖 https://wx.zsxq.com/dweb2/index/topic_detail/123456789 "
                "飞书 https://my.feishu.cn/wiki/example"
            )
        })

        self.assertEqual(len(links), 2)
        self.assertEqual({item["provider"] for item in links}, {"feishu", "zsxq"})

    def test_wechat_and_unknown_web_are_classified_separately(self):
        self.assertEqual(
            external_document_service.provider_for_url(
                "https://mp.weixin.qq.com/s/example"
            ),
            "wechat",
        )
        self.assertEqual(
            external_document_service.provider_for_url(
                "https://example.com/article/42"
            ),
            "generic_web",
        )

    def test_image_assets_are_not_registered_as_documents(self):
        self.assertIsNone(
            external_document_service.provider_for_url(
                "https://example.com/assets/cover.jpg"
            )
        )


class WechatArticleExtractionTest(unittest.TestCase):
    def test_extracts_article_title_author_and_body(self):
        html = """
        <html><head>
          <meta property="og:title" content="测试文章">
          <meta name="author" content="测试作者">
        </head><body>
          <div id="js_content"><p>第一段正文。</p><p>第二段正文。</p></div>
        </body></html>
        """

        title, author, content = external_document_service._extract_wechat_article(html)

        self.assertEqual(title, "测试文章")
        self.assertEqual(author, "测试作者")
        self.assertIn("第一段正文", content)
        self.assertIn("第二段正文", content)

    def test_fetch_does_not_cut_off_late_article_body(self):
        response = SimpleNamespace(
            status_code=200,
            encoding="utf-8",
            text=(
                "<html><head><meta property=\"og:title\" content=\"长页面文章\"></head><body>"
                + ("<!-- style padding -->" * 20000)
                + "<div id=\"js_content\"><p>位于页面后部的真实正文。</p>"
                + "<img src=\"cover.jpg\"/><p>自闭合图片之后的正文仍然存在。</p></div></body></html>"
            ),
            url="https://mp.weixin.qq.com/s/example",
            headers={"content-type": "text/html; charset=utf-8"},
        )
        document = SimpleNamespace(
            canonical_url="https://mp.weixin.qq.com/s/example",
            title=None,
        )

        with patch.object(external_document_service.requests, "get", return_value=response):
            result = external_document_service.ExternalDocumentService()._fetch_wechat_document(document)

        self.assertEqual(result["title"], "长页面文章")
        self.assertIn("位于页面后部的真实正文", result["content_text"])
        self.assertIn("自闭合图片之后的正文仍然存在", result["content_text"])


if __name__ == "__main__":
    unittest.main()
