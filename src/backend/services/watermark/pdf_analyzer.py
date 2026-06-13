import fitz  # type: ignore
import logging
from typing import Dict, Any, List, Optional
from pathlib import Path

# Configure logger
logger = logging.getLogger(__name__)

class PDFAnalyzer:
    """
    PDF 深度解析引擎 (Module 1)
    提供取证级安全审计与结构分析，支持识别隐藏的权限加密。
    """

    @staticmethod
    def analyze(file_path: str, password: str = "") -> Dict[str, Any]:
        """
        全量分析 PDF 文件并生成报告。
        针对瑞银等“隐形加密”进行多维度取证。
        """
        file_p = Path(file_path)
        if not file_p.exists():
            return {"status": "error", "error_msg": f"File not found: {file_path}"}

        report = {
            "status": "success",
            "file_info": {
                "name": file_p.name,
                "size_mb": round(file_p.stat().st_size / (1024 * 1024), 2)
            },
            "security": {
                "is_encrypted": False,
                "encryption_level": "None",
                "requires_password": False,
                "authenticated": False,
                "protection_type": "None"
            },
            "structure": {},
            "metadata": {},
            "verification": {}
        }

        try:
            doc = fitz.open(file_path)
            metadata = doc.metadata or {}

            # 1. 核心取证：判定真实加密状态
            # 瑞银等文件的 doc.is_encrypted 可能是 False，但 meta 中有加密信息
            raw_is_encrypted = doc.is_encrypted
            permissions_raw = doc.permissions
            enc_info_meta = metadata.get("encryption", "None")

            # 综合判定逻辑：只要存在加密元数据或权限受限，即认定为已加密
            real_encrypted = raw_is_encrypted or (enc_info_meta != "None") or (permissions_raw != -1)

            report["security"]["is_encrypted"] = real_encrypted
            report["security"]["encryption_info"] = enc_info_meta
            report["security"]["permissions_raw"] = permissions_raw

            if real_encrypted:
                # 尝试认证
                auth_val = doc.authenticate(password)
                report["security"]["authenticated"] = (auth_val > 0)
                report["security"]["requires_password"] = (auth_val == 0)

                # 判定保护类型
                if not raw_is_encrypted and enc_info_meta != "None":
                    report["security"]["protection_type"] = "Owner Restricted (No User PW)"
                else:
                    report["security"]["protection_type"] = "Standard Password Protected"

            # 2. 权限位解码
            report["security"]["permissions_decoded"] = PDFAnalyzer._decode_permissions(permissions_raw, real_encrypted)

            # 3. 结构分析
            try:
                report["structure"] = {
                    "page_count": len(doc),
                    "pdf_version": metadata.get("format", "Unknown"),
                    "has_outlines": doc.outline is not None,
                    "embedded_files_count": doc.embfile_count(),
                    "is_pdf_a": "PDF/A" in (metadata.get("keywords", "") or ""),
                    "is_linearized": getattr(doc, "is_linearized", "Unknown")
                }
            except Exception:
                report["structure"] = {"error": "Locked: Authentication required."}

            # 4. 元数据
            report["metadata"] = {
                "title": metadata.get("title", ""),
                "author": metadata.get("author", ""),
                "creator": metadata.get("creator", ""),
                "producer": metadata.get("producer", ""),
                "creation_date": metadata.get("creationDate", ""),
                "mod_date": metadata.get("modDate", ""),
            }

            # 5. 推理验证
            if real_encrypted:
                report["verification"] = PDFAnalyzer.verify_inference(doc, report["security"]["permissions_decoded"])

            # 6. 深度取证 (Forensics)
            from services.watermark.pdf_forensics import PDFForensics
            report["forensics"] = PDFForensics.deep_scan(file_path)

            # 7. 智能策略建议 (Smart Strategy) - NEW!
            report["recommendation"] = PDFAnalyzer._generate_strategy(report)

            doc.close()
        except Exception as e:
            logger.error(f"Analysis failed: {str(e)}")
            report["status"] = "error"
            report["error_msg"] = str(e)

        return report

    @staticmethod
    def _generate_strategy(report: Dict[str, Any]) -> Dict[str, Any]:
        """
        基于取证结论，自动生成最佳清洗方案。
        """
        strategy = {
            "recommended_mode": "image_bleach",
            "threshold": 195,
            "text_to_remove": "",
            "do_header_clean": True,
            "do_redaction": True,
            "reason": "常规扫描件清洗"
        }

        forensics = report.get("forensics", {})
        sec = report.get("security", {})

        # 1. 发现隐形指纹 (UBS 类) -> 切换到语义移除模式
        hidden_texts = []
        for pg in forensics.get("hidden_text_found", []):
            for f in pg["findings"]:
                hidden_texts.append(f["text"])

        if hidden_texts:
            strategy["recommended_mode"] = "text_clean"
            strategy["text_to_remove"] = ",".join(list(set(hidden_texts)))
            strategy["reason"] = "检测到隐形追踪指纹，建议执行精准语义移除。"

        # 2. 如果是无读取限制的权限加密 (Owner Restricted)
        if sec.get("protection_type") == "Owner Restricted (No User PW)":
            strategy["do_header_clean"] = True
            strategy["do_redaction"] = True
            if not hidden_texts:
                strategy["reason"] = "检测到所有者权限限制，建议执行极致解密并重构对象。"

        # 3. 如果是纯图片/扫描件 (页码虽多但没搜到文本)
        # TODO: 进一步细化图片判定逻辑

        return strategy

    @staticmethod
    def _decode_permissions(perm_mask: int, is_encrypted: bool) -> Dict[str, bool]:
        """
        深度解码权限位。PDF 标准掩码。
        """
        if not is_encrypted or perm_mask == -1:
            return {
                "print_low_res": True, "modify_contents": True, "copy_contents": True,
                "modify_annotations": True, "fill_forms": True, "accessibility_extract": True,
                "assemble_document": True, "print_high_res": True
            }

        # 特殊处理：如果是瑞银这类权限受限文件，负值掩码需要位运算
        # 标准位：3, 4, 5, 6, 9, 10, 11, 12
        return {
            "print_low_res": bool(perm_mask & (1 << 2)),
            "modify_contents": bool(perm_mask & (1 << 3)),
            "copy_contents": bool(perm_mask & (1 << 4)),
            "modify_annotations": bool(perm_mask & (1 << 5)),
            "fill_forms": bool(perm_mask & (1 << 8)),
            "accessibility_extract": bool(perm_mask & (1 << 9)),
            "assemble_document": bool(perm_mask & (1 << 10)),
            "print_high_res": bool(perm_mask & (1 << 11)),
        }

    @staticmethod
    def verify_inference(doc: fitz.Document, perms: Dict[str, bool]) -> Dict[str, Any]:
        results = {}
        # 瑞银文件的限制通常是“禁止修改内容”
        if not perms.get("modify_contents"):
            results["edit_restriction"] = "Verified: Restricted"
        else:
            results["edit_restriction"] = "Not Restricted"
        return results

if __name__ == "__main__":
    import sys, json
    if len(sys.argv) > 1:
        print(json.dumps(PDFAnalyzer.analyze(sys.argv[1]), indent=4, ensure_ascii=False))
