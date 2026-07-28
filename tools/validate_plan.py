"""Flag grammar defects and repetition in a rewritten content plan.

Run after rewrite_plan.py. Exits non-zero if anything is flagged, so it can
gate the pipeline.

    python tools/validate_plan.py plan/content_plan_fixed.xlsx
"""
import collections
import io
import re
import sys
from pathlib import Path

import openpyxl

sys.path.insert(0, str(Path(__file__).resolve().parent))
from grammar import VERB_WORDS, indefinite_article  # noqa: E402

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

_ARTICLE_PAIR = re.compile(r"\b([Aa]n?) ([A-Za-z][\w'-]*)")


def bad_article(text):
    """True if any indefinite article disagrees with the word after it."""
    return any(
        m.group(1).lower() != indefinite_article(m.group(2))
        for m in _ARTICLE_PAIR.finditer(text)
    )

C_ID, C_TYPE, C_ANIMAL, C_CAPTION, C_ANSWER, C_OVERLAY = 0, 2, 6, 8, 10, 15

# Each check is (label, compiled pattern) applied to caption / overlay / answer.
CHECKS = [
    ("subject with no verb", re.compile(
        r"\bthe [A-Z][\w'-]*(?: [A-Z][\w'-]*)* (a|an|the|one|among|its|both|males|females) ",
    )),
    ("'this animal' + subject", re.compile(
        r"this animal (the|a|an|its|males|females|both|some|each) ", re.I)),
    ("doubled article", re.compile(r"\b(a|an|the) (a|an|the)\b", re.I)),
    ("doubled copula", re.compile(r"\bis (is|are|was)\b", re.I)),
    ("'It <noun phrase>'", re.compile(r"! It (a|an|the|one|among|individual) ")),
    ("stray double space", re.compile(r"  ")),
    ("empty slot", re.compile(r"\{\w+\}")),
    ("naive plural", re.compile(r"\b\w*(fish|sheep|deer)s\b", re.I)),
    ("double colon", re.compile(r": [a-z]+ [a-z]+:")),
    ("doubled lead-in", re.compile(r"\bin (this|some) species,? in \b", re.I)),
    ("repeated phrase", re.compile(r"\b(\w+ \w+ \w+)\b.*\b\1\b", re.I)),
    # "is lays", "is has" -- a copula stacked on a finite verb.
    ("copula + verb", re.compile(
        r"\b(is|are|was|were) (%s)\b" % "|".join(sorted(VERB_WORDS)), re.I)),
]


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else "plan/content_plan_fixed.xlsx"
    wb = openpyxl.load_workbook(path, data_only=True)
    rows = list(wb["Content Calendar"].iter_rows(min_row=2, values_only=True))

    flagged = collections.defaultdict(list)
    for r in rows:
        for col, field in ((C_CAPTION, "caption"), (C_OVERLAY, "overlay"),
                           (C_ANSWER, "answer")):
            text = r[col]
            if not text:
                continue
            for label, pat in CHECKS:
                if pat.search(text):
                    flagged[label].append((r[C_ID], field, text.replace("\n", " ")[:110]))
            if bad_article(text):
                flagged["a/an disagreement"].append(
                    (r[C_ID], field, text.replace("\n", " ")[:110]))

    total = sum(len(v) for v in flagged.values())
    print(f"rows checked: {len(rows)}   flags: {total}\n")
    for label, hits in sorted(flagged.items(), key=lambda kv: -len(kv[1])):
        print(f"-- {label}: {len(hits)}")
        for pid, field, txt in hits[:8]:
            print(f"     [{pid}] {field}: {txt}")

    # repetition: how concentrated are the caption openers?
    facts = [r[C_CAPTION] for r in rows if r[C_TYPE] == "Text Post (Fact)"]
    openers = collections.Counter(" ".join(c.split()[:3]) for c in facts)
    print(f"\nfact posts: {len(facts)}   distinct 3-word openers: {len(openers)}")
    print("most repeated openers:")
    for k, v in openers.most_common(5):
        print(f"   {v:4}  {k}")

    caps = [r[C_CAPTION] for r in rows]
    print(f"\nunique captions: {len(set(caps))} / {len(caps)}")
    ov = [r[C_OVERLAY] for r in rows if r[C_OVERLAY]]
    print(f"unique overlays: {len(set(ov))} / {len(ov)}")
    print(f"longest overlay: {max(len(o) for o in ov)} chars")

    return 1 if total else 0


if __name__ == "__main__":
    sys.exit(main())
