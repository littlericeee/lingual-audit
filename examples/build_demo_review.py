"""Build a deterministic synthetic review from prepared evidence packets."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from lingual_audit.evaluate import predict_from_bundle
from lingual_audit.state import atomic_write_json

RATIONALES = {
    "建议修改": "现有中文与权威证据中的业务术语不一致，建议按证据统一表述。",
    "缺译": "界面当前缺少可用中文译文，应按权威证据补充对应业务术语。",
    "单语限制": "该内容来自单语内嵌区域，当前无法确认可靠的中英文对应关系。",
    "待核对": "现有证据不足以支持确定译法，需要业务人员进一步核对。",
    "来源冲突": "同一适用范围内的权威来源存在不同译法，需要先确认采用口径。",
    "保留": "",
}


def build_review(packets: dict[str, Any]) -> dict[str, Any]:
    reviews = []
    for packet in packets["items"]:
        item = packet["ui_item"]
        prediction = predict_from_bundle(item, packet["evidence_bundle"])
        if item.get("zh_status") == "single_language_embedded":
            prediction = {**prediction, "verdict": "单语限制", "suggested_zh": None}
        elif item.get("zh_status") == "missing_or_raw_key":
            prediction = {**prediction, "verdict": "缺译"}

        verdict = prediction["verdict"]
        reviews.append(
            {
                "item_id": item["item_id"],
                "menu_id": packet["menu_id"],
                "page_id": packet["page_id"],
                "hierarchy_path": item["hierarchy_path"],
                "domain_tag": packet["domain_tag"],
                "concept_id": item.get("concept_id"),
                "en_text": item.get("en_text"),
                "zh_text": item.get("zh_text"),
                "suggested_zh": prediction["suggested_zh"],
                "verdict": verdict,
                "business_rationale": RATIONALES[verdict],
                "evidence_refs": prediction["evidence_refs"],
                "human_review_status": "pending",
            }
        )

    return {
        "contract_version": packets["contract_version"],
        "run_id": "RUN-20260101-0001-demo",
        "corpus_snapshot_hash": packets["corpus_snapshot_hash"],
        "policy_version": packets["policy_version"],
        "reviews": reviews,
        "consistency_findings": [],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("packets_json", type=Path)
    parser.add_argument("output_review_json", type=Path)
    args = parser.parse_args()

    packets = json.loads(args.packets_json.read_text(encoding="utf-8"))
    atomic_write_json(args.output_review_json, build_review(packets))


if __name__ == "__main__":
    main()
