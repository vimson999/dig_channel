import fitz  # type: ignore
import os
import logging
from typing import List, Tuple
from pathlib import Path

logger = logging.getLogger(__name__)

class PDFSplitter:
    """
    PDF 结构化处理引擎 (Module 2)
    支持物理页码拆分、逻辑书签拆分等无损操作。
    """

    @staticmethod
    def split_by_range(file_path: str, output_dir: str, ranges: List[Tuple[int, int]]) -> List[str]:
        """
        根据指定的页码范围拆分 PDF。
        ranges: [(start_page, end_page), ...] (1-based index)
        """
        if not os.path.exists(file_path):
            raise FileNotFoundError(f"Input file not found: {file_path}")

        os.makedirs(output_dir, exist_ok=True)
        generated_files = []

        try:
            src_doc = fitz.open(file_path)
            base_name = Path(file_path).stem

            for i, (start, end) in enumerate(ranges):
                # 转换为 0-based 索引
                # fitz 的页面提取是 [start, end)
                new_doc = fitz.open()
                # 检查范围合法性
                start_idx = max(0, start - 1)
                end_idx = min(len(src_doc), end)

                if start_idx >= end_idx:
                    continue

                new_doc.insert_pdf(src_doc, from_page=start_idx, to_page=end_idx-1)

                output_filename = f"{base_name}_part_{i+1}_{start}_{end}.pdf"
                output_path = os.path.join(output_dir, output_filename)

                new_doc.save(output_path)
                new_doc.close()
                generated_files.append(output_filename)

            src_doc.close()
            return generated_files

        except Exception as e:
            logger.error(f"PDF Split error: {str(e)}")
            raise

    @staticmethod
    def split_equally(file_path: str, output_dir: str, step: int) -> List[str]:
        """
        将 PDF 按固定页数等分。
        """
        src_doc = fitz.open(file_path)
        total_pages = len(src_doc)
        src_doc.close()

        ranges = []
        for start in range(1, total_pages + 1, step):
            end = min(start + step - 1, total_pages)
            ranges.append((start, end))

        return PDFSplitter.split_by_range(file_path, output_dir, ranges)
