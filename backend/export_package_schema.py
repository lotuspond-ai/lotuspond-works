"""Generate the declarative package JSON schema without a database or runtime."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from lotuspond_sdk.contract import WorkflowPackage


def render() -> str:
    return json.dumps(WorkflowPackage.model_json_schema(), indent=2, ensure_ascii=False) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    destination = Path(__file__).resolve().parents[1] / "contracts/workflow-package.v1.schema.json"
    rendered = render()
    if args.check:
        if not destination.is_file() or destination.read_text(encoding="utf-8") != rendered:
            raise SystemExit("Package schema differs from the model. Regenerate and review it.")
    else:
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(rendered, encoding="utf-8")
        print("Generated contracts/workflow-package.v1.schema.json without execution.")


if __name__ == "__main__":
    main()
