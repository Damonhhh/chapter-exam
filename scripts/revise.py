"""仅替换指定题目的可编辑内容，保存可再次出卷的 JSON。"""
import argparse
import json
import os
import tempfile
from pathlib import Path

from embed import verify_paper


def revise(paper, patch, ids):
    verify_paper(paper)
    meta = paper.get("meta", paper)
    if patch.get("title") != meta["title"] or patch.get("course") != meta.get("course", ""):
        raise ValueError("换题内容与当前卷名或课程不符。")
    changes = patch.get("replacements")
    if not isinstance(changes, list) or not changes or not ids or len(set(ids)) != len(ids):
        raise ValueError("请指定本次换题的 ID。")
    by_id = {item["id"]: item for item in changes}
    if len(by_id) != len(changes) or set(by_id) != set(ids):
        raise ValueError("返回的题目与指定范围不同。")
    revised = []
    for i, original in enumerate(paper["questions"], 1):
        qid = original.get("id", f"q{i}")
        if qid not in by_id:
            revised.append(original)
            continue
        item = by_id.pop(qid)
        fields = item["changes"]
        if item["before"] != original["ask"] or fields.get("ask") == original["ask"]:
            raise ValueError(f"{qid} 原题干不匹配，或题目未改。")
        required = {"ask", "answer", "rubric", "trap"}
        if original.get("type") in ("选择题", "选择", "单选"):
            required.update(("options", "key"))
        if original.get("accept"):
            required.add("accept")
        if not required <= fields.keys() <= required | {"accept"}:
            raise ValueError(f"{qid} 缺必要字段或试图修改题型、分数等固定内容。")
        if not all(isinstance(fields[k], str) and fields[k].strip() for k in ("ask","answer","trap")):
            raise ValueError(f"{qid} 缺少新题干、答案或易错处。")
        if "accept" in fields and not isinstance(fields["accept"], list):
            raise ValueError(f"{qid} 的 accept 应为数组。")
        updated = {**original, **fields}
        updated.pop("model", None)
        revised.append(updated)
    if by_id:
        raise ValueError("当前卷子找不到指定题目。")
    result = {**paper, "questions": revised}
    verify_paper(result)
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paper", type=Path)
    parser.add_argument("patch", type=Path)
    parser.add_argument("--ids", nargs="+", required=True)
    parser.add_argument("-o", "--out", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.out.resolve() in {args.paper.resolve(), args.patch.resolve()}:
            raise ValueError("请用新文件名保存，保留原卷。")
        paper, patch = (json.loads(p.read_text(encoding="utf-8-sig")) for p in (args.paper,args.patch))
        result = revise(paper, patch, args.ids)
        args.out.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=args.out.parent, delete=False) as f:
            temp = Path(f.name)
        try:
            temp.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            os.replace(temp, args.out)
        finally:
            temp.unlink(missing_ok=True)
        print(args.out.resolve())
    except (ValueError, KeyError, TypeError, AttributeError, OSError) as exc:
        parser.exit(1, f"换题失败：{exc}\n")
