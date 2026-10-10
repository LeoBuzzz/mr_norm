"""Count tokens in a prepared classification prompt.

tiktoken is an OpenAI tokenizer. Grok's tokenizer differs, so the script
also prints a character-based estimate of 2.0 characters per token, which
matched this corpus on the earlier calibration.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import tiktoken


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("prompt", type=Path)
    parser.add_argument("--cards", type=int, required=True)
    args = parser.parse_args()
    text = args.prompt.read_text(encoding="utf-8")
    enc = tiktoken.get_encoding("o200k_base")
    tokens = len(enc.encode(text))
    report = {
        "prompt_chars": len(text),
        "cards": args.cards,
        "tiktoken_o200k": tokens,
        "chars_per_token_estimate": round(len(text) / 2.0),
        "tiktoken_per_card": round(tokens / args.cards, 2),
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
