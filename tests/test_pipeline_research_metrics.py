"""Unit tests for retrieval metrics in scripts/run_pipeline_research.py."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = ROOT / "scripts" / "run_pipeline_research.py"


def _load_research_module():
    spec = importlib.util.spec_from_file_location("run_pipeline_research", SCRIPT_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["run_pipeline_research"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def research():
    return _load_research_module()


def _item(**kwargs):
    from mr_norm.retrieval.contracts import RetrievedItem

    defaults = {
        "chunk_id": "",
        "doc_id": "",
        "doc_name": "",
        "point_number": "",
        "text": "sample",
        "source_tool": "hybrid",
    }
    defaults.update(kwargs)
    return RetrievedItem(**defaults)


def test_analyze_retrieval_hits_chunk_doc_point(research):
    evidence = [
        _item(chunk_id="other", doc_id="d2", doc_name="Doc B", point_number="1.2"),
        _item(chunk_id="gold", doc_id="d1", doc_name="Doc A", point_number="3.4"),
        _item(chunk_id="same-doc", doc_id="d1", doc_name="Doc A", point_number="9.9"),
    ]
    hits = research.analyze_retrieval_hits(
        evidence,
        chunk_id="gold",
        doc_id="d1",
        doc_name="Doc A",
        point_number="3.4",
    )
    assert hits["chunk_in_evidence"] is True
    assert hits["chunk_rank"] == 2
    assert hits["doc_in_evidence"] is True
    assert hits["doc_rank"] == 2
    assert hits["point_in_evidence"] is True
    assert hits["point_rank"] == 2


def test_analyze_retrieval_hits_doc_name_substring(research):
    evidence = [
        _item(chunk_id="x1", doc_id="", doc_name="ПТЭ — Правила технической эксплуатации", point_number=""),
    ]
    hits = research.analyze_retrieval_hits(
        evidence,
        chunk_id="missing",
        doc_id="",
        doc_name="ПТЭ",
        point_number="",
    )
    assert hits["chunk_in_evidence"] is False
    assert hits["doc_in_evidence"] is True
    assert hits["doc_rank"] == 1


def test_build_top_evidence_snapshot(research):
    evidence = [
        _item(chunk_id="c1", doc_name="Doc", point_number="1", text="a" * 400, score=0.9),
    ]
    snapshot = research.build_top_evidence_snapshot(evidence, limit=5, text_limit=50)
    assert len(snapshot) == 1
    assert snapshot[0]["rank"] == 1
    assert snapshot[0]["chunk_id"] == "c1"
    assert len(snapshot[0]["text"]) <= 51


def test_analyze_final_evidence_deterministic(research):
    evidence = [
        _item(chunk_id="g1", doc_name="ГОСТ Р 57114", score=0.8),
        _item(chunk_id="g2", doc_name="ГОСТ Р 57114", score=0.7),
        _item(chunk_id="d1", doc_name="ПТЭ — Правила", score=0.6),
    ]
    stats = research.analyze_final_evidence_deterministic(
        evidence,
        limit=3,
        resolved_doc_name="ПТЭ",
    )
    assert stats["final_evidence_count"] == 3
    assert stats["gost_chunks_top5"] == 2
    assert stats["chunks_from_resolved_doc"] == 1


def test_normalize_chunk_judgments(research):
    snapshot = [
        {"rank": 1, "chunk_id": "c1", "doc_name": "Doc", "point_number": "1"},
        {"rank": 2, "chunk_id": "c2", "doc_name": "Doc", "point_number": "2"},
    ]
    normalized = research.normalize_chunk_judgments(
        [
            {"chunk_id": "c2", "relevance_score": 4, "useful_for_answer": False, "issue": "noise"},
            {"chunk_id": "missing", "relevance_score": 9, "useful_for_answer": True, "issue": "none"},
            {"chunk_id": "c1", "relevance_score": 11, "useful_for_answer": True, "issue": "good"},
        ],
        evidence_snapshot=snapshot,
    )
    assert len(normalized) == 2
    assert normalized[0]["chunk_id"] == "c1"
    assert normalized[0]["relevance_score"] == 10
    assert normalized[1]["issue"] == "noise"


@pytest.mark.parametrize(
    ("score", "equivalent", "reason", "expected"),
    [
        (2, True, "ok", True),
        (8, True, "ok", False),
        (3, False, "ошибка в оценке: score завышен", True),
        (5, False, "нормально", False),
    ],
)
def test_detect_judge_score_mismatch(research, score, equivalent, reason, expected):
    assert research.detect_judge_score_mismatch(score, equivalent, reason) is expected


def test_compute_metrics_includes_retrieval_fields(research):
    entries = [
        {
            "judge_score": 8,
            "judge_equivalent": True,
            "chunk_in_evidence": True,
            "doc_in_evidence": True,
            "point_in_evidence": True,
            "point_number": "1.2",
            "evidence_count": 40,
            "evidence_judge_score": 7,
            "evidence_judge_can_answer": True,
            "useful_chunks_count": 3,
            "noise_chunks_count": 1,
            "gost_chunks_top5": 0,
            "chunks_from_resolved_doc": 2,
            "judge_score_mismatch": False,
            "question_type": "factual",
            "difficulty": "easy",
        },
        {
            "judge_score": 3,
            "judge_equivalent": False,
            "chunk_in_evidence": False,
            "doc_in_evidence": True,
            "point_in_evidence": False,
            "point_number": "2.3",
            "evidence_count": 35,
            "evidence_judge_score": 4,
            "evidence_judge_can_answer": False,
            "useful_chunks_count": 1,
            "noise_chunks_count": 4,
            "gost_chunks_top5": 3,
            "chunks_from_resolved_doc": 0,
            "judge_score_mismatch": True,
            "question_type": "definition",
            "difficulty": "hard",
        },
    ]
    metrics = research._compute_metrics(entries)
    assert metrics["doc_in_evidence_rate"] == 1.0
    assert metrics["chunk_in_evidence_rate"] == 0.5
    assert metrics["point_in_evidence_rate"] == 0.5
    assert metrics["mean_evidence_judge_score"] == 5.5
    assert metrics["mean_useful_chunks"] == 2.0
    assert metrics["mean_noise_chunks"] == 2.5
    assert metrics["mean_gost_chunks_top5"] == 1.5
    assert metrics["judge_mismatch_rate"] == 0.5
