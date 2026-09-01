import tempfile
import unittest
from pathlib import Path

import fitz

from services.report_video_director import build_director_package
from services.report_video_director_enrichment import (
    DirectorEnrichmentError,
    attach_audio_timeline,
    build_mixed_media_asset_plan,
    derive_verified_pdf_evidence_map,
    enrich_package_evidence,
)


class ReportVideoDirectorEnrichmentTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.pdf = self.root / "real.pdf"
        document = fitz.open()
        page = document.new_page()
        page.insert_text(
            (72, 96),
            "Exhibit 1: Glass inventory declined to 35 days.",
        )
        document.save(self.pdf)
        document.close()
        self.audio = self.root / "real.wav"
        self.audio.write_bytes(b"RIFF" + b"\0" * 256)
        material = {
            "job_id": "source-001",
            "pdf": {
                "attachment_id": "attachment-001",
                "filename": "real.pdf",
                "path": str(self.pdf),
            },
            "audio_path": str(self.audio),
            "structured_result": {
                "title": "玻璃库存验证",
                "final_script_for_tts": (
                    "玻璃库存正在下降，供给侧冷修已经开始改变短期平衡。"
                    "报告显示库存已经降至三十五天，价格也出现了连续回升。"
                    "图表显示玻璃盈利率改善，但其他产业链环节仍然偏弱。"
                    "最后仍需跟踪终端需求和组件出口变化带来的风险。"
                ),
            },
        }
        evidence = [
            {
                "evidence_id": "ev-001",
                "segment_index": index,
                "page": 1,
                "kind": "table",
                "quote": "Exhibit 1: Glass inventory declined to 35 days.",
            }
            for index in range(1, 5)
        ]
        self.package = build_director_package(
            material,
            evidence,
            execution_mode="test",
            target_segment_chars=60,
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_real_pdf_evidence_is_verified_and_fingerprinted(self):
        enriched = enrich_package_evidence(
            self.package,
            pdf_path=str(self.pdf),
        )

        summary = enriched["evidence_enrichment"]
        self.assertEqual(summary["unverified_refs"], 0)
        self.assertGreater(summary["verified_refs"], 0)
        self.assertFalse(summary["fail_closed"])
        first = enriched["segments"][0]["evidence_refs"][0]
        self.assertEqual(first["verification"]["status"], "verified")
        self.assertEqual(
            first["verification"]["visual_hint"],
            "chart_or_table_focus",
        )

    def test_legacy_material_derives_only_verbatim_real_pdf_evidence(self):
        document = fitz.open()
        page = document.new_page()
        page.insert_textbox(
            fitz.Rect(72, 72, 520, 420),
            (
                "Exhibit 1: Token throughput increased 320 times while memory "
                "bandwidth increased only 14 percent. This creates a material "
                "memory bottleneck for artificial intelligence infrastructure. "
                "The report estimates memory spending could reach 40 percent "
                "of cloud capital expenditure by 2027 and highlights several "
                "technology paths with different execution risks."
            ),
            fontsize=11,
        )
        derived_pdf = self.root / "legacy-real.pdf"
        document.save(derived_pdf)
        document.close()
        material = {
            "structured_result": {
                "final_script_for_tts": (
                    "令牌吞吐量增长三百二十倍，但内存带宽只增长百分之十四。"
                    "到二零二七年，内存开支可能占云资本支出的百分之四十。"
                    "最后需要关注不同技术路线的执行风险和兑现节奏。"
                )
            }
        }

        evidence = derive_verified_pdf_evidence_map(
            material,
            pdf_path=str(derived_pdf),
            target_segment_chars=60,
        )

        self.assertTrue(evidence)
        self.assertTrue(all(item["page"] == 1 for item in evidence))
        self.assertTrue(
            all(
                item["mapping"]["verbatim_pdf_text"] is True
                for item in evidence
            )
        )
        package = build_director_package(
            material,
            evidence,
            execution_mode="test",
            target_segment_chars=60,
        )
        enriched = enrich_package_evidence(
            package,
            pdf_path=str(derived_pdf),
        )
        self.assertEqual(enriched["evidence_enrichment"]["unverified_refs"], 0)

    def test_production_timeline_rejects_character_estimate(self):
        with self.assertRaises(DirectorEnrichmentError):
            attach_audio_timeline(
                self.package,
                audio_path=str(self.audio),
                audio_seconds=10,
            )

    def test_existing_alignment_builds_production_ready_asset_plan(self):
        enriched = enrich_package_evidence(
            self.package,
            pdf_path=str(self.pdf),
        )
        segments = enriched["segments"]
        unit = 12 / len(segments)
        alignment = {
            "timeline": [
                {
                    "start_seconds": index * unit,
                    "end_seconds": (index + 1) * unit,
                    "duration_seconds": unit,
                    "confidence": 0.92,
                }
                for index in range(len(segments))
            ]
        }
        timed = attach_audio_timeline(
            enriched,
            audio_path=str(self.audio),
            audio_seconds=12,
            alignment=alignment,
        )
        plan = build_mixed_media_asset_plan(timed)

        self.assertTrue(timed["audio_timeline"]["production_ready"])
        self.assertTrue(plan["production_ready"])
        self.assertEqual(plan["dispatch_default"], "blocked_until_mode_gate")
        self.assertFalse(plan["publish_allowed"])
        self.assertTrue(
            all(
                item["full_asset_generation_allowed"] is False
                for item in plan["items"]
            )
        )


if __name__ == "__main__":
    unittest.main()
