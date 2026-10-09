from __future__ import annotations

from evaluate_router_v3_h3_semantic_replay_r10 import _regressions_against


def _row(omissions):
    return {"case_id": "T01", "score": {"plan_match": False, "critical_omissions": omissions}}


def test_r10_new_unmatched_task_is_regression_even_when_code_already_exists():
    task0 = {"code": "unmatched_expected_task", "task_index": 0,
             "closest_actual_task_index": 0, "failed_fields": ["query_semantics"]}
    task1 = {"code": "unmatched_expected_task", "task_index": 1,
             "closest_actual_task_index": None, "failed_fields": []}
    report = _regressions_against([_row([task0, task1])], [_row([task0])])
    assert report["no_new_critical_regressions"] is False
    assert report["added_critical_records"] == [{"case_id": "T01", "record": {
        "code": "unmatched_expected_task", "task_index": 1, "failed_fields": []}}]


def test_r10_changed_failed_fields_is_a_distinct_critical_regression():
    reference = {"code": "unmatched_expected_task", "task_index": 0,
                 "failed_fields": ["query_semantics"]}
    candidate = {"code": "unmatched_expected_task", "task_index": 0,
                 "failed_fields": ["query_semantics", "semantic_elements"]}
    report = _regressions_against([_row([candidate])], [_row([reference])])
    assert report["no_new_critical_regressions"] is False
    assert report["added_critical_records"][0]["record"]["failed_fields"] == [
        "query_semantics", "semantic_elements"]


def test_r10_reordering_and_transient_closest_match_do_not_create_regression():
    ref_task = {"code": "unmatched_expected_task", "task_index": 0,
                "closest_actual_task_index": 2, "failed_fields": ["query_semantics", "conditions"]}
    ref_edge = {"code": "dependency_edges_mismatch", "source_task_index": 0,
                "target_task_index": 1, "failed_fields": ["edge_b", "edge_a"]}
    candidate_edge = {"failed_fields": ["edge_a", "edge_b"], "target_task_index": 1,
                      "source_task_index": 0, "code": "dependency_edges_mismatch"}
    candidate_task = {"failed_fields": ["conditions", "query_semantics"],
                      "closest_actual_task_index": 9, "task_index": 0,
                      "code": "unmatched_expected_task"}
    report = _regressions_against([_row([candidate_edge, candidate_task])], [_row([ref_task, ref_edge])])
    assert report["no_new_critical_regressions"] is True
    assert report["added_critical_records"] == []
