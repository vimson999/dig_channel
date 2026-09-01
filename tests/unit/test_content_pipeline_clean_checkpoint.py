import json
from pathlib import Path

from services.content_pipeline.clean_checkpoint import load_completed_clean_result


def _write_manifest(batch_dir: Path, *, sources: list[Path], cleaned_items: list[dict]) -> Path:
    output_dir = batch_dir / "pdf_cleaned"
    output_dir.mkdir(parents=True, exist_ok=True)
    manifest = batch_dir / "manifest.json"
    manifest.write_text(
        json.dumps({
            "batch_id": batch_dir.name,
            "local_output_dir": str(output_dir),
            "files": [
                {
                    "status": "SUCCESS",
                    "source_path": str(source),
                    "output_path": item["output_path"],
                }
                for source, item in zip(sources, cleaned_items)
            ],
            "cleaned_files": cleaned_items,
            "success_count": len(cleaned_items),
            "failed_count": 0,
        }),
        encoding="utf-8",
    )
    return manifest


def test_checkpoint_rejects_cleaned_list_that_omits_a_successful_input(tmp_path: Path):
    sources = [tmp_path / "a.pdf", tmp_path / "b.pdf"]
    for source in sources:
        source.write_bytes(b"%PDF-1.4\nsource")

    batch_dir = tmp_path / "batch"
    output_dir = batch_dir / "pdf_cleaned"
    output_dir.mkdir(parents=True)
    outputs = [output_dir / "a.pdf", output_dir / "b.pdf"]
    for output in outputs:
        output.write_bytes(b"%PDF-1.4\ncleaned")

    only_first = [{
        "status": "SUCCESS",
        "source_path": str(sources[0]),
        "output_path": str(outputs[0]),
    }]
    manifest = _write_manifest(batch_dir, sources=sources, cleaned_items=only_first)
    document = json.loads(manifest.read_text(encoding="utf-8"))
    document["files"] = [
        {
            "status": "SUCCESS",
            "source_path": str(source),
            "output_path": str(output),
        }
        for source, output in zip(sources, outputs)
    ]
    manifest.write_text(json.dumps(document), encoding="utf-8")

    assert load_completed_clean_result(
        manifest,
        batch_id="batch",
        input_files=sources,
        output_dir=output_dir,
        allowed_suffixes={".pdf"},
    ) is None


def test_checkpoint_accepts_exact_success_output_mapping(tmp_path: Path):
    sources = [tmp_path / "a.pdf", tmp_path / "b.pdf"]
    for source in sources:
        source.write_bytes(b"%PDF-1.4\nsource")
    batch_dir = tmp_path / "batch"
    output_dir = batch_dir / "pdf_cleaned"
    output_dir.mkdir(parents=True)
    outputs = [output_dir / "a.pdf", output_dir / "b.pdf"]
    cleaned_items = []
    for source, output in zip(sources, outputs):
        output.write_bytes(b"%PDF-1.4\ncleaned")
        cleaned_items.append({
            "status": "SUCCESS",
            "source_path": str(source),
            "output_path": str(output),
        })
    manifest = _write_manifest(batch_dir, sources=sources, cleaned_items=cleaned_items)

    resumed = load_completed_clean_result(
        manifest,
        batch_id="batch",
        input_files=sources,
        output_dir=output_dir,
        allowed_suffixes={".pdf"},
    )

    assert resumed is not None
    assert resumed["resumed_from_checkpoint"] is True
