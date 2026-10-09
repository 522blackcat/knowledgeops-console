"""
从真实语料抽样生成 RAG 评测用例。

历史用例是手写题库，与当前上传文档不对应，
所以用例改为从文档自身抽取：
问句行作为 query，问句原文行作为 anchor_text。

anchor_text 是锚点而非期望词，
所以改分块规则后同一套标签仍然可比。
问句被切成两块时锚点会失效，
这类用例计入 anchor_missing，用作分块质量的直接信号。

用法：
    python -m evaluation.rag_cases_sampler \
        --corpus-dir "C:/Users/.../面试" \
        --files a.docx b.docx \
        --out evaluation/rag_eval_cases_v3.json \
        --per-file 80
"""

from __future__ import annotations

import argparse
import json
import random
import re
from pathlib import Path

from rag.parser import parse_document

QUESTION_LINE = re.compile(r"^(?P<num>\d{1,3}[\.、\)]\s*)?(?P<body>.+[？?])\s*$")
SKIP_PREFIX = re.compile(r"^(Q\d+|问题\s*\d+|第\s*\d+\s*[题问])", re.IGNORECASE)


def extract_questions(text: str) -> list[str]:
    questions: list[str] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or len(line) > 120:
            continue
        if line.count("|") >= 2:
            continue
        match = QUESTION_LINE.match(line)
        if not match:
            continue
        body = match.group("body").strip()
        if len(body) < 6:
            continue
        questions.append(line)
    return questions


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--corpus-dir", type=Path, required=True)
    parser.add_argument("--files", nargs="+", required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--per-file", type=int, default=80)
    parser.add_argument("--seed", type=int, default=20261007)
    args = parser.parse_args()

    rng = random.Random(args.seed)
    cases: list[dict[str, object]] = []
    report: dict[str, object] = {"per_file": {}}
    seen: set[str] = set()

    for index, name in enumerate(args.files, start=1):
        path = args.corpus_dir / name
        sections = parse_document(path)
        questions = extract_questions("\n".join(section.text for section in sections))

        unique: list[str] = []
        for question in questions:
            key = re.sub(r"\s+", "", question)
            if key in seen:
                continue
            seen.add(key)
            unique.append(question)

        picked = (
            unique
            if len(unique) <= args.per_file
            else rng.sample(unique, args.per_file)
        )
        (report["per_file"]) [name] = {
            "抽取问句": len(questions),
            "跨文件去重后": len(unique),
            "抽样": len(picked),
        }

        for order, question in enumerate(sorted(picked), start=1):
            match = QUESTION_LINE.match(question)
            body = (match.group("body") or question).strip()
            cases.append(
                {
                    "id": f"{path.stem[:12]}-{index:02d}-{order:03d}",
                    "query": body,
                    "anchor_text": question,
                    "source_file": name,
                    "knowledge_scope": "global",
                    "min_hits": 1,
                }
            )

    payload = {
        "name": "uploaded_docs_qa_v3",
        "description": (
            "从上传文档的问句行自动抽样的检索评测用例。"
            "anchor_text 为文档中的问句原文，用于定位相关块。"
        ),
        "generated_from": args.files,
        "sampling": {"per_file": args.per_file, "seed": args.seed},
        "stats": report,
        "cases": cases,
    }
    args.out.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps({"cases": len(cases), **report}, ensure_ascii=False, indent=2))
    print(f"输出: {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
