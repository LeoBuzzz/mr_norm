from __future__ import annotations

from mr_norm.retrieval.contracts import RetrievedItem
from mr_norm.retrieval.doc_point_topic_index import (
    DocPointTopicIndex,
    TopicEntry,
    TopicRerankPolicy,
    compute_idf,
    merge_topic_retry_items,
    rerank_with_doc_point_topics,
    search_doc_point_topics,
    select_topic_rerank_policy,
    select_topic_retry_policy,
    should_apply_topic_rerank,
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


def make_neighbor_index() -> DocPointTopicIndex:
    entries = (
        TopicEntry(
            topic_id="topic_general",
            doc_id="doc_a",
            doc_name="Правила технической эксплуатации",
            point_number="3.1",
            heading_path_text="Общие положения",
            chunk_ids=("chunk_general",),
            text="3.1. Общие требования к эксплуатации оборудования.",
            point_topic="Общие требования.",
            tokens=("общие", "требования", "эксплуатация", "оборудование"),
        ),
        TopicEntry(
            topic_id="topic_duty",
            doc_id="doc_a",
            doc_name="Правила технической эксплуатации",
            point_number="3.2",
            heading_path_text="Обязанности персонала",
            chunk_ids=("chunk_duty",),
            text="3.2. Персонал обязан выполнять инструкции по охране труда.",
            point_topic="Обязанности персонала.",
            natural_aliases=("обязан", "обязанности", "персонал"),
            tokens=("персонал", "обязан", "обязанности", "инструкции", "охрана", "труда", "эксплуатация"),
        ),
    )
    return DocPointTopicIndex(entries=entries, idf=compute_idf(entries), source_path="test")


def make_table_index() -> DocPointTopicIndex:
    entries = (
        TopicEntry(
            topic_id="topic_text",
            doc_id="doc_table",
            doc_name="Норматив по нагрузкам",
            point_number="2",
            heading_path_text="Описание",
            chunk_ids=("chunk_text",),
            text="2. Нагрузки должны учитываться при проектировании.",
            tokens=("нагруз", "проектирование"),
        ),
        TopicEntry(
            topic_id="topic_table",
            doc_id="doc_table",
            doc_name="Норматив по нагрузкам",
            point_number="5",
            heading_path_text="Таблица параметров",
            chunk_ids=("chunk_table",),
            text="5. Параметры: температура 40 °C; нагрузка 10 МВт; эксцентриситет 0,5 %.",
            point_topic="Таблица параметров нагрузки.",
            tokens=("параметр", "температур", "нагруз", "эксцентриситет", "мвт"),
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


def test_should_apply_topic_rerank_for_obligation_marker() -> None:
    do_rerank, reason = should_apply_topic_rerank(
        query="Какие обязанности у персонала?",
        question_type="requirement",
        ranked_items=[RetrievedItem(chunk_id="x", source_tool="hybrid_rrf")],
    )

    assert do_rerank is True
    assert reason.startswith("topic_rerank:")


def test_rerank_promotes_specific_point_above_neighbor() -> None:
    index = make_neighbor_index()
    baseline = [
        RetrievedItem(
            chunk_id="chunk_general",
            doc_id="doc_a",
            doc_name="Правила технической эксплуатации",
            point_number="3.1",
            text="3.1. Общие требования.",
            score=0.95,
            source_tool="hybrid_rrf",
        ),
        RetrievedItem(
            chunk_id="noise",
            doc_id="doc_b",
            doc_name="Other",
            point_number="1",
            score=0.5,
            source_tool="hybrid_rrf",
        ),
    ]
    policy = select_topic_rerank_policy(
        "Какие обязанности у персонала при эксплуатации?",
        question_type="requirement",
        resolved_doc_names=("Правила технической эксплуатации",),
        ranked_items=baseline,
    )
    policy = TopicRerankPolicy(
        search_profile=policy.search_profile,
        doc_ids=policy.doc_ids,
        doc_names=policy.doc_names,
        top_k=policy.top_k,
        min_promote_score=1.0,
        table_like=policy.table_like,
    )

    result = rerank_with_doc_point_topics(
        "Какие обязанности у персонала при эксплуатации?",
        baseline,
        index,
        policy=policy,
    )

    assert result.applied is True
    assert result.ranked_items[0].point_number == "3.2"
    assert result.ranked_items[0].chunk_id == "chunk_duty"
    assert any(item.chunk_id == "chunk_general" for item in result.ranked_items)


def test_rerank_doc_scope_prefers_resolved_document() -> None:
    index = make_index()
    baseline = [
        RetrievedItem(
            chunk_id="chunk_noise",
            doc_id="doc_noise",
            doc_name="Правила учета электрической энергии",
            point_number="4.4",
            score=0.99,
            source_tool="hybrid_rrf",
        ),
    ]
    policy = TopicRerankPolicy(
        search_profile="tuned",
        doc_ids=("doc_remote",),
        doc_names=("Требования безопасности при дистанционном управлении",),
        top_k=8,
        min_promote_score=2.0,
    )

    result = rerank_with_doc_point_topics(
        "Разрешен ли доступ из интернета для дистанционного управления?",
        baseline,
        index,
        policy=policy,
    )

    assert result.applied is True
    assert result.ranked_items[0].doc_id == "doc_remote"


def test_rerank_table_like_prefers_numeric_entry() -> None:
    index = make_table_index()
    baseline = [
        RetrievedItem(
            chunk_id="chunk_text",
            doc_id="doc_table",
            doc_name="Норматив по нагрузкам",
            point_number="2",
            score=0.9,
            source_tool="hybrid_rrf",
        ),
    ]
    policy = select_topic_rerank_policy(
        "По каким параметрам нагрузки указаны в таблице?",
        question_type="factual",
        resolved_doc_names=("Норматив по нагрузкам",),
        ranked_items=baseline,
    )
    policy = TopicRerankPolicy(
        search_profile=policy.search_profile,
        doc_ids=policy.doc_ids,
        doc_names=policy.doc_names,
        top_k=policy.top_k,
        min_promote_score=1.0,
        table_like=True,
    )

    result = rerank_with_doc_point_topics(
        "По каким параметрам нагрузки указаны в таблице?",
        baseline,
        index,
        policy=policy,
    )

    assert result.applied is True
    assert result.ranked_items[0].chunk_id == "chunk_table"


def test_rerank_skips_weak_topic_hits() -> None:
    index = make_index()
    baseline = [
        RetrievedItem(chunk_id="baseline", doc_id="doc_x", score=0.8, source_tool="hybrid_rrf"),
    ]
    policy = TopicRerankPolicy(min_promote_score=99.0)

    result = rerank_with_doc_point_topics(
        "случайный запрос без совпадений",
        baseline,
        index,
        policy=policy,
    )

    assert result.applied is False
    assert result.reason == "topic_rerank:weak_hits"
    assert result.ranked_items[0].chunk_id == "baseline"


def test_rerank_dedupes_existing_baseline_chunk() -> None:
    index = make_neighbor_index()
    baseline = [
        RetrievedItem(
            chunk_id="chunk_duty",
            doc_id="doc_a",
            doc_name="Правила технической эксплуатации",
            point_number="3.2",
            score=0.4,
            source_tool="hybrid_rrf",
        ),
        RetrievedItem(
            chunk_id="chunk_general",
            doc_id="doc_a",
            doc_name="Правила технической эксплуатации",
            point_number="3.1",
            score=0.95,
            source_tool="hybrid_rrf",
        ),
    ]
    policy = select_topic_rerank_policy(
        "Какие обязанности у персонала?",
        question_type="requirement",
        ranked_items=baseline,
    )
    policy = TopicRerankPolicy(
        search_profile=policy.search_profile,
        doc_ids=policy.doc_ids,
        doc_names=policy.doc_names,
        top_k=policy.top_k,
        min_promote_score=1.0,
        table_like=policy.table_like,
    )

    result = rerank_with_doc_point_topics(
        "Какие обязанности у персонала?",
        baseline,
        index,
        policy=policy,
    )

    chunk_ids = [item.chunk_id for item in result.ranked_items]
    assert chunk_ids.count("chunk_duty") == 1
    assert result.items_promoted >= 1


def test_should_apply_topic_rerank_skips_when_baseline_doc_satisfied() -> None:
    ranked = [
        RetrievedItem(
            chunk_id="chunk_a",
            doc_name="О комплексном определении показателей технико-экономического состояния",
            score=0.9,
            source_tool="hybrid_rrf",
        ),
    ]
    do_rerank, reason = should_apply_topic_rerank(
        query="Когда нужно рассчитать показатели не позднее 31 марта?",
        question_type="factual",
        ranked_items=ranked,
        resolved_doc_names=("О комплексном определении показателей технико-экономического состояния",),
    )

    assert do_rerank is False
    assert reason == "topic_rerank:baseline_doc_satisfied"


def test_should_attempt_topic_retry_skips_after_successful_rerank() -> None:
    do_retry, reason = should_attempt_topic_retry(
        query="Какие обязанности у персонала?",
        final_warnings=[],
        has_citations=True,
        topic_rerank_applied=True,
    )

    assert do_retry is False
    assert reason == "topic_retry:rerank_satisfied"
