#!/usr/bin/env python3
"""Export the viewer's route-generated schema without a database or network connection.

Run with the viewer dependencies installed. Output defaults to build/openapi.json;
use --output PATH for release artifacts, or --check to compare an existing export.
"""
import argparse
import importlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]


def load_app():
    sys.path.insert(0, str(ROOT))
    sys.path.insert(0, str(ROOT / 'services/viewer'))
    return importlib.import_module('main').app


def render():
    return json.dumps(load_app().openapi(), indent=2, ensure_ascii=False) + '\n'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT / 'build/openapi.json')
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args()
    text = render()
    if args.check:
        if not args.output.exists() or args.output.read_text(encoding='utf-8') != text:
            print(f'{args.output} is missing or stale; regenerate it with --output {args.output}', file=sys.stderr)
            return 1
    else:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding='utf-8')
    print(f'{args.output}: {len(json.loads(text)["paths"])} paths')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
