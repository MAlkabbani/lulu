from __future__ import annotations

import argparse
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config import Settings
from memory_manager import MemoryHit, MemoryManager
from ollama_client import OllamaClient


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Inspect Lulu's canonical long-term memory in text mode."
    )
    parser.add_argument(
        "--query",
        help="Run a semantic search instead of listing all stored memories.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=10,
        help="Maximum number of memories to display.",
    )
    parser.add_argument(
        "--show-metadata",
        action="store_true",
        help="Print timestamps and normalized text for each memory.",
    )
    return parser.parse_args()


def format_hit(hit: MemoryHit, index: int, show_metadata: bool) -> str:
    tags = ", ".join(hit.tags) if hit.tags else "general"
    source = str(hit.metadata.get("source", "unknown"))
    updated_at = str(hit.metadata.get("updated_at", "unknown"))
    similarity_text = (
        f" similarity={hit.similarity:.3f}" if hit.similarity is not None else ""
    )
    lines = [
        f"{index}. id={hit.id}{similarity_text}",
        f"   source={source} tags=[{tags}]",
        f"   text={hit.text}",
    ]
    if show_metadata:
        normalized_text = str(hit.metadata.get("normalized_text", ""))
        lines.append(f"   updated_at={updated_at}")
        lines.append(f"   normalized_text={normalized_text}")
    return "\n".join(lines)


def list_memories(manager: MemoryManager, limit: int) -> list[MemoryHit]:
    return manager.list_recent_memories(limit)


def main() -> None:
    args = parse_args()
    settings = Settings()
    client = OllamaClient(settings)
    manager = MemoryManager(settings, client)

    if args.query:
        hits = manager.query_memory(args.query, k=args.limit)
        heading = f"Semantic matches for: {args.query}"
    else:
        hits = list_memories(manager, args.limit)
        heading = f"Latest canonical memories (limit={args.limit})"

    print(heading)
    print("=" * len(heading))
    if not hits:
        print("No memories found.")
        return

    for index, hit in enumerate(hits, start=1):
        print(format_hit(hit, index=index, show_metadata=args.show_metadata))
        print()


if __name__ == "__main__":
    main()
