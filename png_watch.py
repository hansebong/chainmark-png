"""PyCharm: run without arguments, then copy PNGs between A/B/C folders."""
import argparse
import json
from pathlib import Path
import sys

from chainmark.pngwatch import PNGWorkspace, initialize, watch, write_json

ROOT = Path(__file__).resolve().parent


def main(argv=None):
    parser = argparse.ArgumentParser(description="ChainMark PNG folder-arrival experiment (no web UI)")
    sub = parser.add_subparsers(dest="command")
    init = sub.add_parser("init", help="Create a NEW workspace and clean PNG reference")
    init.add_argument("--workspace", required=True, type=Path)
    init.add_argument("--source", type=Path, help="Optional own PNG; otherwise generate a synthetic drawing")
    watcher = sub.add_parser("watch", help="Watch A/B/C until Ctrl+C or a stop request")
    watcher.add_argument("--workspace", required=True, type=Path)
    watcher.add_argument("--interval", type=float, default=.2)
    watcher.add_argument("--settle", type=float, default=.4)
    inspect = sub.add_parser("inspect", help="Detect pixels and verify registered lineage separately")
    inspect.add_argument("--workspace", required=True, type=Path)
    inspect.add_argument("--image", required=True, type=Path)
    inspect.add_argument("--output", type=Path)
    stop = sub.add_parser("stop", help="Request a running watcher to stop cleanly")
    stop.add_argument("--workspace", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command is None:
            root = ROOT / "png_workspace"
            if not (root/".chainmark"/"config.json").exists():
                initialize(root)
            watch(root)
        elif args.command == "init":
            print(initialize(args.workspace, args.source))
        elif args.command == "watch":
            watch(args.workspace, args.interval, args.settle)
        elif args.command == "inspect":
            workspace = PNGWorkspace(args.workspace)
            try:
                result = workspace.inspect(args.image)
                print(json.dumps(result, indent=2, ensure_ascii=False))
                if args.output:
                    args.output.parent.mkdir(parents=True, exist_ok=True)
                    write_json(args.output, result)
            finally:
                workspace.close()
        elif args.command == "stop":
            state = args.workspace/".chainmark"
            if not (state/"config.json").exists():
                raise ValueError("Not an initialized PNG workspace")
            (state/"stop.request").touch()
            print("Stop requested")
        return 0
    except (ValueError, OSError, RuntimeError) as exc:
        print(f"ChainMark PNG: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
