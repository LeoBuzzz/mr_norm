"""Transfer the bot's Qdrant collection between machines as JSONL.

The export contains vectors, payloads and payload index types. It is intentionally
separate from Git: the resulting files can be large and contain document text.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
from pathlib import Path


ARTIFACTS = (
    Path("output/qdrant_chunks.json"),
    Path("output/document_catalog.json"),
    Path("tmp/knowledge_catalog_mapping.json"),
)


def transfer_artifacts(directory: Path, *, restore: bool) -> None:
    root = Path(__file__).resolve().parents[1]
    for relative in ARTIFACTS:
        source = directory / "artifacts" / relative if restore else root / relative
        target = root / relative if restore else directory / "artifacts" / relative
        if source.is_file():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
            print(f"{'Restored' if restore else 'Exported'} {relative}")


def client():
    from qdrant_client import QdrantClient

    return QdrantClient(
        url=f"http://{os.getenv('QDRANT_HOST', 'localhost')}:{os.getenv('QDRANT_PORT', '6333')}",
        api_key=os.getenv("QDRANT_API_KEY") or None,
        timeout=120,
    )


def _model_json(value):
    return value.model_dump(mode="json") if hasattr(value, "model_dump") else value.dict()


def export_collection(directory: Path, collection: str) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    metadata_path = directory / "collection.json"
    points_path = directory / "points.jsonl"
    if metadata_path.exists() or points_path.exists():
        raise FileExistsError(f"Export already exists in {directory}; choose an empty directory")

    qdrant = client()
    info = qdrant.get_collection(collection)
    vectors = info.config.params.vectors
    if not isinstance(vectors, dict):
        raise ValueError("Only named dense vectors are supported by this transfer script")
    metadata = {
        "format": "mr_norm_bot_qdrant_v1",
        "collection": collection,
        "vectors": {name: _model_json(params) for name, params in vectors.items()},
        "payload_indexes": {
            name: str(getattr(schema.data_type, "value", schema.data_type)).lower()
            for name, schema in (info.payload_schema or {}).items()
        },
    }

    written = 0
    offset = None
    temp_points = directory / "points.jsonl.tmp"
    try:
        with temp_points.open("w", encoding="utf-8") as output:
            while True:
                points, offset = qdrant.scroll(
                    collection_name=collection,
                    limit=128,
                    offset=offset,
                    with_payload=True,
                    with_vectors=True,
                )
                for point in points:
                    output.write(json.dumps({"id": point.id, "vector": point.vector, "payload": point.payload}, ensure_ascii=False) + "\n")
                    written += 1
                if offset is None:
                    break
        live_count = qdrant.count(collection_name=collection, exact=True).count
        if written != live_count:
            raise RuntimeError(f"Export count mismatch: written={written}, Qdrant={live_count}")
        metadata["points_count"] = written
        temp_points.replace(points_path)
        metadata_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    finally:
        temp_points.unlink(missing_ok=True)
    transfer_artifacts(directory, restore=False)
    print(f"Exported {written} points from {collection} to {directory}")


def import_collection(directory: Path, rebuild: bool) -> None:
    from qdrant_client import models

    metadata = json.loads((directory / "collection.json").read_text(encoding="utf-8"))
    if metadata.get("format") != "mr_norm_bot_qdrant_v1":
        raise ValueError("Unsupported export format")
    collection = metadata["collection"]
    qdrant = client()
    exists = qdrant.collection_exists(collection)
    if exists and not rebuild:
        raise FileExistsError(f"Collection {collection} exists; use --rebuild to replace it")
    if exists:
        qdrant.delete_collection(collection)
    qdrant.create_collection(
        collection_name=collection,
        vectors_config={name: models.VectorParams(**params) for name, params in metadata["vectors"].items()},
    )

    count = 0
    batch = []
    with (directory / "points.jsonl").open("r", encoding="utf-8") as source:
        for line in source:
            item = json.loads(line)
            batch.append(models.PointStruct(id=item["id"], vector=item["vector"], payload=item.get("payload")))
            if len(batch) >= 128:
                qdrant.upsert(collection_name=collection, points=batch, wait=True)
                count += len(batch)
                batch.clear()
        if batch:
            qdrant.upsert(collection_name=collection, points=batch, wait=True)
            count += len(batch)

    schemas = {"keyword": models.PayloadSchemaType.KEYWORD, "text": models.PayloadSchemaType.TEXT,
               "integer": models.PayloadSchemaType.INTEGER, "float": models.PayloadSchemaType.FLOAT,
               "bool": models.PayloadSchemaType.BOOL, "datetime": models.PayloadSchemaType.DATETIME,
               "geo": models.PayloadSchemaType.GEO}
    for field, kind in metadata.get("payload_indexes", {}).items():
        if kind not in schemas:
            raise ValueError(f"Unsupported payload index {field}: {kind}")
        qdrant.create_payload_index(collection_name=collection, field_name=field, field_schema=schemas[kind], wait=True)

    actual = qdrant.count(collection_name=collection, exact=True).count
    expected = metadata["points_count"]
    if count != expected or actual != expected:
        raise RuntimeError(f"Point count mismatch: file={expected}, read={count}, Qdrant={actual}")
    transfer_artifacts(directory, restore=True)
    print(f"Imported and verified {actual} points in {collection}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    export = subparsers.add_parser("export")
    export.add_argument("--directory", type=Path, required=True)
    export.add_argument("--collection", default=os.getenv("MR_NORM_QDRANT_COLLECTION", "mr_norm_docs_bge_m3_rtf_v2_20261011"))
    imp = subparsers.add_parser("import")
    imp.add_argument("--directory", type=Path, required=True)
    imp.add_argument("--rebuild", action="store_true")
    args = parser.parse_args()
    if args.command == "export":
        export_collection(args.directory, args.collection)
    else:
        import_collection(args.directory, args.rebuild)


if __name__ == "__main__":
    main()
