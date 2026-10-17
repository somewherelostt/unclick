"""Run the Unclick pipeline from the command line.

    python scripts/run_agent.py --url https://projects.dev/leaderboard --task "Get the top projects with name, votes and URL"
    python scripts/run_agent.py --fake            # fake sandbox + desktop (local), real LLM if OPENAI_API_KEY is set
    python scripts/run_agent.py --fake --scripted # fully offline: no CreateOS, no LLM
"""

from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv  # noqa: E402

load_dotenv()

from unclick import pipeline  # noqa: E402
from unclick.contracts import PrintSink  # noqa: E402

DEFAULT_URL = "https://projects.dev/leaderboard"
DEFAULT_TASK = "Get the top projects with name, votes and URL"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--url", default=DEFAULT_URL)
    ap.add_argument("--task", default=DEFAULT_TASK)
    ap.add_argument("--fake", action="store_true", help="use unclick.fakes for the sandbox + desktop layer")
    ap.add_argument("--scripted", action="store_true", help="use the scripted agent instead of the LLM")
    args = ap.parse_args()

    kwargs = {}
    if args.fake:
        from unclick import fakes
        kwargs.update(factory=fakes.FakeFactory(), desktop_factory=fakes.fake_desktop_factory)
    if args.scripted or (args.fake and not os.getenv("OPENAI_API_KEY")):
        from unclick import fakes
        if not args.scripted:
            print("[note] OPENAI_API_KEY not set: using the scripted agent", flush=True)
        kwargs["agent"] = fakes.ScriptedAgent()

    sink = PrintSink()
    result = pipeline.run(args.url, args.task, sink, **kwargs)
    print("\n=== RunResult ===")
    print(f"ok={result.ok} attempts={result.attempts} endpoint={result.endpoint}")
    if result.error:
        print(f"error={result.error}")
    if result.api:
        print(f"api={result.api.api_url}\ncurl: {result.api.curl}")
    if result.sample_output is not None:
        print("sample:", json.dumps(result.sample_output, ensure_ascii=False)[:800])
    return 0 if result.ok else 1


if __name__ == "__main__":
    sys.exit(main())
