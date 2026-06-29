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


if __name__ == "__main__":
    unittest.main()
