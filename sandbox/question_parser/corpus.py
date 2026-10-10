"""Local point text for the sandbox skills.

The skills read these records directly. Nothing here talks to a vector store
or the network.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class LocalPoint:
    doc_id: str
    point_ref: str
    text: str


@dataclass
class LocalCorpus:
    points: list[LocalPoint] = field(default_factory=list)
    point_reads: int = 0

    def point(self, doc_id: str, point_ref: str) -> str | None:
        self.point_reads += 1
        for item in self.points:
            if item.doc_id == doc_id and item.point_ref == point_ref:
                return item.text
        return None

    def matching(self, doc_id: str | None = None) -> list[LocalPoint]:
        if doc_id is None:
            return list(self.points)
        return [item for item in self.points if item.doc_id == doc_id]


def build_demo_corpus(catalog) -> LocalCorpus:
    """Small readable corpus keyed by the definition catalog's own document ids."""
    gost = next(doc for doc in catalog.documents if "19431" in doc.name and "гост" in doc.name.casefold())
    pues = [doc for doc in catalog.documents if "пуэ" in doc.name.casefold()]
    first, second = pues[0], pues[1]
    return LocalCorpus(
        [
            LocalPoint(gost.doc_id, "2", "Электроэнергетика охватывает производство электрической энергии."),
            LocalPoint(gost.doc_id, "3", "Линия электропередачи воздушных опор соединяет станцию и сеть."),
            LocalPoint(gost.doc_id, "4", "Линия электропередачи кабельных сетей проходит в земле."),
            LocalPoint(gost.doc_id, "5", "Опора воздушной линии стоит отдельно от кабельной трассы."),
            LocalPoint(first.doc_id, "1.2.18", "Категория электроприемников задает надежность снабжения."),
            LocalPoint(second.doc_id, "1.7.1", "Заземление выполняют у электроустановок этой главы."),
        ]
    )
