"""Tests for natural-style corpus generation helpers."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = ROOT / "scripts" / "run_pipeline_research.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("run_pipeline_research", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules["run_pipeline_research"] = module
    spec.loader.exec_module(module)
    return module


mod = _load_module()


def test_question_has_doc_references_detects_act_citation():
    assert mod.question_has_doc_references(
        "Какие требования предъявляются согласно Приказу Минэнерго № 894?"
    )
    assert not mod.question_has_doc_references(
        "Какие требования предъявляются к хранению документации в электронном виде?"
    )


def test_fallback_natural_question_avoids_doc_names():
    chunk = {
        "text": "19. Хранение документации в электронном виде должно обеспечивать защиту от несанкционированного доступа.",
        "payload": {"doc_name": "Приказ Минэнерго №894", "point_number": "19"},
    }
    question = mod.fallback_natural_question(chunk, question_type="requirement")
    assert not mod.question_has_doc_references(question)
    assert "894" not in question
