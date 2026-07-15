import sys
import unittest
from pathlib import Path
from unittest.mock import patch


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


if __name__ == "__main__":
    unittest.main()
