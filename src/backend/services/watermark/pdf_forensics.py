import fitz  # type: ignore
import logging
from typing import Dict, Any, List
from pathlib import Path

logger = logging.getLogger(__name__)

class PDFForensics:
    """
    PDF 深度取证引擎 (Forensic Module)
    探测隐形文字、透明层、图层水印及加工痕迹。
    """

    @staticmethod
    def deep_scan(file_path: str) -> Dict[str, Any]:
        report = {
            "hidden_text_found": [],
            "ocg_layers": [],
            "suspicious_objects": [],
            "processing_traces": []
        }

        try:
            doc = fitz.open(file_path)

            # 1. 探测 OCG 图层 (Optional Content Groups)
            # 水印常隐藏在这些逻辑图层中
            ocgs = doc.get_ocgs()
            if ocgs:
                for xref, name in ocgs.items():
                    report["ocg_layers"].append({"xref": xref, "name": name})

            # 2. 逐页进行内容取证
            for page_index in range(len(doc)):
                page = doc[page_index]
                page_report = {"page": page_index + 1, "findings": []}

                # 获取该页所有文本字典，包含颜色、透明度、大小
                blocks = page.get_text("dict")["blocks"]
                for b in blocks:
                    if "lines" not in b: continue
                    for l in b["lines"]:
                        for s in l["spans"]:
                            # A. 探测隐形/透明文字
                            # opacity=0 或者文字颜色与白色过于接近
                            color_hex = f"#{s['color']:06x}"
                            is_hidden = False
                            reason = ""

                            # 判定逻辑 1: 字号极小 (追踪指纹)
                            if s["size"] < 1.0:
                                is_hidden = True
                                reason = "Micro-text (Trace Fingerprint)"

                            # 判定逻辑 2: 白色文字 (同色遮盖)
                            if color_hex.upper() in ["#FFFFFF", "#FEFEFE"]:
                                is_hidden = True
                                reason = "White-on-White (Invisible)"

                            if is_hidden:
                                page_report["findings"].append({
                                    "text": s["text"],
                                    "reason": reason,
                                    "font": s["font"],
                                    "origin": s["origin"]
                                })

                if page_report["findings"]:
                    report["hidden_text_found"].append(page_report)

            # 3. 扫描对象流中的加工指纹
            # 查找 PieceInfo (常用于保存水印软件的私有数据)
            for xref in range(1, doc.xref_length()):
                obj_content = doc.xref_object(xref)
                if "/PieceInfo" in obj_content or "/Watermark" in obj_content:
                    report["processing_traces"].append({
                        "xref": xref,
                        "type": "Metadata Trace",
                        "content": obj_content[:200] + "..."
                    })

            doc.close()
        except Exception as e:
            logger.error(f"Forensic scan failed: {e}")
            report["error"] = str(e)

        return report
