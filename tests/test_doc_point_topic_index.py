from __future__ import annotations

from mr_norm.retrieval.contracts import RetrievedItem
from mr_norm.retrieval.doc_point_topic_index import (
    DocPointTopicIndex,
    TopicEntry,
    compute_idf,
    merge_topic_retry_items,
    search_doc_point_topics,
    select_topic_retry_policy,
    should_attempt_topic_retry,
    topic_hit_to_retrieved_item,
)


def make_index() -> DocPointTopicIndex:
    entries = (
        TopicEntry(
            topic_id="topic_remote",
            doc_id="doc_remote",
            doc_name="Требования безопасности при дистанционном управлении",
            point_number="9",
            heading_path_text="Дистанционное управление",
            chunk_ids=("chunk_remote",),
            text=(
                "9. Не допускается подключение технологических сетей связи к сети Интернет "
                "при дистанционном управлении объектами электроэнергетики."
            ),
            doc_summary="Документ регулирует безопасность дистанционного управления.",
            point_topic="Не допускается подключение к сети Интернет.",
            natural_aliases=("разрешено ли", "дистанционное управление", "удаленный доступ"),
            tokens=(
                "дистанционное",
                "управление",
                "интернет",
                "подключение",
                "запрещается",
                "допускается",
            ),
        ),
        TopicEntry(
            topic_id="topic_noise",
            doc_id="doc_noise",
            doc_name="Правила учета электрической энергии",
            point_number="4.4",
            heading_path_text="Удаленный доступ",
            chunk_ids=("chunk_noise",),
            text="Удаленный доступ пользователей к функциям системы учета осуществляется через сеть.",
            point_topic="Удаленный доступ к системе учета.",
            natural_aliases=("удаленный доступ",),
            tokens=("удаленный", "доступ", "система", "учет"),
        ),
    )
    return DocPointTopicIndex(entries=entries, idf=compute_idf(entries), source_path="test")


def test_search_doc_point_topics_prefers_specific_point() -> None:
    index = make_index()

    hits = search_doc_point_topics(
        "Разрешен ли доступ из интернета для дистанционного управления?",
        index,
        profile="tuned",
    )

    assert hits
    assert hits[0].entry.doc_id == "doc_remote"
    assert hits[0].entry.point_number == "9"


def test_topic_hit_to_retrieved_item_keeps_source_details() -> None:
    hit = search_doc_point_topics("интернет дистанционное управление", make_index(), profile="tuned")[0]

    item = topic_hit_to_retrieved_item(hit)

    assert item.chunk_id == "chunk_remote"
    assert item.doc_id == "doc_remote"
    assert item.source_tool == "topic_index"
    assert item.matched["topic_id"] == "topic_remote"


def test_select_topic_retry_policy_uses_topic_only_for_prohibition() -> None:
    policy = select_topic_retry_policy("Что запрещено делать в охранных зонах электрических сетей?")

    assert policy.search_profile == "tuned"
    assert policy.evidence_policy == "topic_only"


def test_merge_topic_retry_items_can_prioritize_topic_only() -> None:
    topic_item = RetrievedItem(chunk_id="topic", doc_id="doc_a", source_tool="topic_index")
    baseline_item = RetrievedItem(chunk_id="baseline", doc_id="doc_b", source_tool="hybrid_rrf")

    merged, added = merge_topic_retry_items(
        topic_items=[topic_item],
        ranked_items=[baseline_item],
        policy="topic_only",
        limit=10,
    )

    assert [item.chunk_id for item in merged] == ["topic"]
    assert added == 1


def test_should_attempt_topic_retry_for_weak_answer() -> None:
    do_retry, reason = should_attempt_topic_retry(
        query="Разрешен ли доступ из интернета?",
        final_warnings=["final answer returned no valid citations"],
        has_citations=False,
    )

    assert do_retry is True
    assert reason == "topic_retry:no_valid_citations"
