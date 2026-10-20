#!/usr/bin/env python3
"""Measure cold prompt and generation throughput at exact context fractions."""
import argparse
import datetime as dt
import json
import math
from pathlib import Path
import time
import urllib.request


def request(base, route, payload):
    req = urllib.request.Request(base + route, json.dumps(payload).encode(),
                                 {"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=3600) as response:
        return json.load(response)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://localhost:8080")
    parser.add_argument("--context", type=int, default=73728)
    parser.add_argument("--fractions", type=float, nargs="+", default=[.01, .25, .5, .75, .95])
    parser.add_argument("--predict", type=int, default=128)
    parser.add_argument("--output", type=Path, default=Path("RESULTS.json"))
    args = parser.parse_args()

    text = "\n".join(
        f"Archive record {i}: parcel {i * 2654435761 & 0xffffffff:08x} was checked at station {i % 97}."
        for i in range(30000)
    )
    filler = request(args.url, "/tokenize", {"content": text, "add_special": False})["tokens"]
    results = []
    for fraction in args.fractions:
        prompt_tokens = math.floor(args.context * fraction)
        if prompt_tokens + args.predict > args.context:
            raise ValueError(f"{fraction:.0%}: prompt plus output exceeds context")
        prompt = (filler * math.ceil(prompt_tokens / len(filler)))[:prompt_tokens]
        started = time.monotonic()
        response = request(args.url, "/completion", {
            "prompt": prompt, "n_predict": args.predict, "temperature": 0,
            "seed": 42, "ignore_eos": True, "cache_prompt": False,
        })
        timings = response["timings"]
        row = {
            "fraction": fraction,
            "prompt_tokens_requested": prompt_tokens,
            "prompt_tokens_measured": timings.get("prompt_n"),
            "predicted_tokens": timings.get("predicted_n"),
            "prompt_tokens_per_second": timings.get("prompt_per_second"),
            "generation_tokens_per_second": timings.get("predicted_per_second"),
            "elapsed_seconds": time.monotonic() - started,
        }
        results.append(row)
        print(json.dumps(row), flush=True)
    record = {"created_at": dt.datetime.now(dt.timezone.utc).isoformat(),
              "url": args.url, "context": args.context, "predict": args.predict,
              "results": results}
    args.output.write_text(json.dumps(record, indent=2) + "\n")


if __name__ == "__main__":
    main()
