#!/usr/bin/env python3
"""Example: call a DeepSeek model through NVIDIA's hosted OpenAI-compatible API.

The API key is read from the NVAPI_KEY environment variable and is NEVER
hardcoded here. Set it from your .env (gitignored) or your shell:

    export NVAPI_KEY="nvapi-..."
    python3 scripts/examples/nvidia_deepseek_limerick.py

Requires the `openai` package (present in the project venv).
"""
import os
import sys
import traceback

from openai import OpenAI

MODEL = "deepseek-ai/deepseek-v4-pro-0813"


def main() -> int:
    api_key = os.getenv("NVAPI_KEY")
    if not api_key:
        print("NVAPI_KEY not set. Export it before running, or add it to .env.", file=sys.stderr)
        return 2

    client = OpenAI(
        base_url="https://integrate.api.nvidia.com/v1",
        api_key=api_key,
    )

    try:
        completion = client.chat.completions.create(
            model=MODEL,
            messages=[
                {"role": "user", "content": "Write a limerick about the wonders of GPU computing."}
            ],
            temperature=1,
            top_p=0.95,
            max_tokens=16384,
            seed=42,
            extra_body={"chat_template_kwargs": {"thinking": False}},
            stream=False,
        )
    except Exception:  # noqa: BLE001 - surface the real error to the operator
        traceback.print_exc()
        return 1

    print(completion.choices[0].message.content)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
