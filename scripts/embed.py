"""生成内置题库的离线卷，按需对照用户提供的原件。"""
import argparse
import json
import os
import re
import tempfile
import unicodedata
from pathlib import Path

MARK = "<script>\n/* ============================ CORE:START"
TEMPLATE = Path(__file__).resolve().parents[1] / "assets" / "paper.html"


def json_text(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)

def pdf_text(value):
    return "".join(unicodedata.normalize("NFKC", value).split())


def verify_paper(paper):
    if not isinstance(paper, dict) or "part" in paper or "of" in paper:
        raise ValueError("请提供合并后的完整题库文件。聊天分批请在组卷器依次载入。")
    questions = paper.get("questions") or []
    meta = paper.get("meta", paper)
    if not questions or not meta.get("title"):
        raise ValueError("缺卷名或题目。")
    if sum(q["points"] for q in questions) != meta.get("total"):
        raise ValueError("全卷总分与题分合计不一致。")
    topics = paper["topics"]
    ids = {t["id"] for t in topics}
    if len(ids) != len(topics) or not all(ids):
        raise ValueError("考点编号为空或重复。")
    used, asked, qids = set(), set(), set()
    for n, q in enumerate(questions, 1):
        stem = re.sub(r"\s+", "", q["ask"])
        rubric = q.get("rubric") or []
        qid = q.get("id", "q" + str(n))
        if qid in qids or not stem or stem in asked or not q.get("answer") or not q.get("topics"):
            raise ValueError(f"第 {n} 题缺内容或与其他题重复。")
        if q["points"] <= 0 or not rubric or sum(r["points"] for r in rubric) != q["points"]:
            raise ValueError(f"第 {n} 题分值或评分点合计有误。")
        if any(r["points"] < 0 or not str(r.get("for") or r.get("reason") or "").strip() for r in rubric):
            raise ValueError(f"第 {n} 题评分点缺理由或为负数。")
        if q.get("type") in ("选择", "选择题"):
            letters = [v.strip()[:1].upper() for v in q.get("options", [])]
            if len(letters) < 2 or len(set(letters)) != len(letters) or q.get("key","").upper() not in letters:
                raise ValueError(f"第 {n} 题的选项与答案字母对不上。")
        asked.add(stem)
        qids.add(qid)
        used.update(q["topics"])
    if ids != used:
        raise ValueError("考点与各题引用不一致。")
    for topic in topics:
        ref = topic.get("ref") or {}
        if ref.get("source") in (None,"","none"):
            continue
        page, line = ref.get("page", ref.get("at",0)), ref.get("line",0)
        if ref["source"] not in paper.get("sources",{}) or not str(ref.get("quote","")).strip():
            raise ValueError(f'{topic["id"]} 缺材料或引文。')
        if any(type(v) is not int or v < 0 for v in (page,line)) or not (page or line):
            raise ValueError(f'{topic["id"]} 缺有效的页码或行号。')


def read_units(path):
    if path.suffix.lower() == ".pdf":
        try:
            from pypdf import PdfReader
        except ImportError:
            raise ValueError("此环境没有 pypdf。可省略此 PDF 的 --source 并人工核对，卷上将标为待核对。") from None
        return "page", [p.extract_text() or "" for p in PdfReader(path).pages]
    if path.suffix.lower() not in (".txt",".md"):
        raise ValueError("自动比对支持 TXT、Markdown 和可提取文字的 PDF。")
    return "line", path.read_text(encoding="utf-8-sig").splitlines()


def match_sources(paper, supplied):
    receipts = {}
    for name, path in supplied.items():
        if name not in paper.get("sources", {}):
            raise ValueError(f"找不到材料编号 {name}。")
        kind, units = read_units(path)
        for t in paper["topics"]:
            ref = t.get("ref") or {}
            if ref.get("source") != name:
                continue
            page = ref.get("page",ref.get("at",0))
            line = ref.get("line",0)
            pos = page if kind == "page" else line
            if not pos or (kind == "line" and page):
                raise ValueError(f'{t["name"]} 的定位方式与原件格式不符。')
            quote = ref["quote"].strip()
            unit = units[pos-1] if 1 <= pos <= len(units) else ""
            if kind == "page":
                matches = pdf_text(quote) in pdf_text(unit)
            else:
                matches = quote in unit
            if not matches:
                raise ValueError(f'{t["name"]} 在 {path.name} 的第 {pos} {"页" if kind=="page" else "行"} 找不到引文。')
            receipts[json_text([name,page,line,quote])] = "ok"
    return receipts


def render(paper, template=TEMPLATE, supplied=None):
    verify_paper(paper)
    checks = match_sources(paper, supplied or {})
    html = template.read_text(encoding="utf-8-sig")
    if html.count(MARK) != 1:
        raise ValueError("模板缺少唯一的题库入口。")
    def literal(data):
        return json_text(data).replace("<",r"\u003c")
    payload = "<script>window.__PAPER__=" + literal(paper) + ";window.__PAPER_CHECKS__=" + literal(checks) + ";</script>\n"
    return html.replace(MARK,payload+MARK,1)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paper",type=Path)
    parser.add_argument("-o","--out",type=Path,required=True)
    parser.add_argument("--template",type=Path,default=TEMPLATE)
    parser.add_argument("--source",action="append",default=[],metavar="编号=原件路径")
    args=parser.parse_args()
    try:
        sources={}
        for entry in args.source:
            key, sep, path = entry.partition("=")
            if not key or not sep or not path or key in sources:
                raise ValueError("--source 使用 编号=原件路径，每份材料指定一次。")
            sources[key]=Path(path)
        if args.out.resolve() in {p.resolve() for p in [args.paper,args.template,*sources.values()]}:
            raise ValueError("输出文件与输入材料同名。")
        content=render(json.loads(args.paper.read_text(encoding="utf-8-sig")), args.template, sources)
        args.out.parent.mkdir(parents=True,exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=args.out.parent,delete=False) as f:
            tmp=Path(f.name)
        try:
            with tmp.open("w",encoding="utf-8",newline="\n") as f:
                f.write(content)
            os.replace(tmp,args.out)
        finally:
            tmp.unlink(missing_ok=True)
        print(args.out.resolve())
    except (OSError,ValueError,TypeError,KeyError,AttributeError) as exc:
        parser.exit(1,f"生成失败：{exc}\n")


if __name__ == "__main__":
    main()
