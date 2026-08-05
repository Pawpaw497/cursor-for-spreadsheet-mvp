"""CLI：``cd server && uv run python -m evaluation``。

真实调用 LLM（本地 Ollama 或云端 OpenRouter，取决于 --model-source），
不进 CI，用法见 docs/evaluation.md。
"""
from __future__ import annotations

import argparse
import json
import sys

from .cases import CASES
from .runner import print_report, run_all

# 与 server/app/config.py OPENROUTER_MODEL 默认一致；不传 --cloud-model-id 时用此 id。
DEFAULT_CLOUD_MODEL_ID = "deepseek/deepseek-v4-pro"


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m evaluation",
        description="Run the Agent quality eval suite against the local sample dataset.",
    )
    parser.add_argument(
        "--model-source",
        choices=["local", "cloud"],
        default="cloud",
        help="Cloud (default) uses OpenRouter with the product default model.",
    )
    parser.add_argument(
        "--cloud-model-id",
        default=DEFAULT_CLOUD_MODEL_ID,
        help=f"OpenRouter model id (cloud only). Default: {DEFAULT_CLOUD_MODEL_ID}",
    )
    parser.add_argument("--local-model-id", default=None, help="Ollama model id (default: server config).")
    parser.add_argument(
        "--case",
        dest="case_ids",
        action="append",
        default=None,
        help=f"Case id to run (repeatable). Available: {', '.join(c.id for c in CASES)}",
    )
    parser.add_argument(
        "--json-out",
        default=None,
        help="Optional path to write a machine-readable report (for manual before/after diffing).",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    results = run_all(
        model_source=args.model_source,
        cloud_model_id=args.cloud_model_id,
        local_model_id=args.local_model_id,
        case_ids=args.case_ids,
    )
    exit_code = print_report(results)

    if args.json_out:
        payload = [
            {
                "id": r.id,
                "title": r.title,
                "passed": r.passed,
                "failures": r.failures,
                "elapsed_ms": r.elapsed_ms,
                "step_count": r.step_count,
                "error": r.error,
            }
            for r in results
        ]
        with open(args.json_out, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        print(f"\nWrote JSON report to {args.json_out}")

    return exit_code


if __name__ == "__main__":
    sys.exit(main())
