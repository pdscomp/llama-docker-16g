#!/usr/bin/env python3
import argparse
import json
import os
from pathlib import Path

from huggingface_hub import HfApi, snapshot_download


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("repo")
    parser.add_argument("files", nargs="*")
    parser.add_argument("--revision", default="main")
    args = parser.parse_args()
    token = os.environ.get("HF_TOKEN") or None
    resolved = HfApi(token=token).model_info(args.repo, revision=args.revision).sha
    path = snapshot_download(args.repo, revision=resolved, allow_patterns=args.files or None,
                             token=token, cache_dir=os.environ["HF_HUB_CACHE"])
    print(json.dumps({"repo": args.repo, "requested_revision": args.revision,
                      "revision": resolved, "path": str(Path(path).resolve())}))


if __name__ == "__main__":
    main()

