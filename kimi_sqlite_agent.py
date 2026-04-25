from __future__ import annotations

import argparse

import config
from admin_ai import DEFAULT_MODEL, ask_kimi, run_registered_tool


def run_tool(name: str, args: dict[str, object], *, db_path: str | None = None):
    return run_registered_tool(name, args, db_path=db_path)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run a Kimi-to-SQLite agent query against Montana Blotter data.")
    parser.add_argument("question", help="Question to ask the Kimi agent.")
    parser.add_argument("--model", default=DEFAULT_MODEL, help=f"Kimi model name (default: {DEFAULT_MODEL})")
    parser.add_argument("--db-path", default=config.DB_PATH, help="SQLite database path.")
    args = parser.parse_args()

    answer = ask_kimi(args.question, model=args.model, db_path=args.db_path)
    print(answer)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
