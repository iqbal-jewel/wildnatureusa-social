"""Rewrite the WildNatureUSA content plan: repair grammar, vary phrasing.

Reads the source workbook, decomposes every caption back into (animal, fact
type, semantic core), then recomposes it through a much wider template pool
chosen by the predicate's grammatical shape. Fixes:

  * predicates glued onto a subject without a linking verb
  * "a" / "an" disagreement before vowel-initial animal names
  * naive pluralisation ("Moon Jellyfishs")
  * ~10 rotating openers across 736 fact posts

Writes a corrected workbook plus a QA report of anything it could not place.

    python tools/rewrite_plan.py <source.xlsx> <output.xlsx>
"""
import collections
import io
import re
import sys
from pathlib import Path

import openpyxl

sys.path.insert(0, str(Path(__file__).resolve().parent))
from grammar import (  # noqa: E402
    HAS, NOUN, OWN, POSS, PREDICATE_OVERRIDES, VERB, WAS, cap, classify,
    fix_articles, indefinite_article,
)

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

# Column indices in the Content Calendar sheet (0-based).
C_ID, C_PLATFORM, C_TYPE, C_DATE = 0, 1, 2, 3
C_ANIMAL, C_CAPTION, C_QUESTION, C_ANSWER = 6, 8, 9, 10
C_OVERLAY = 15

# --- parsing the source ---------------------------------------------------
HABITAT_PATTERNS = [
    re.compile(r"^Did you know\? The (?P<a>.+?) lives in (?P<x>.+)\.$"),
    re.compile(r"^Home turf: the (?P<a>.+?) is found in (?P<x>.+)\.$"),
    re.compile(r"^Habitat fact: the (?P<a>.+?) makes its home in (?P<x>.+)\.$"),
]
DIET_PATTERNS = [
    re.compile(r"^What's on the menu\? The (?P<a>.+?) feeds on (?P<x>.+)\.$"),
    re.compile(r"^Dinner time: the (?P<a>.+?) dines on (?P<x>.+)\.$"),
    re.compile(r"^Fact: (?P<a>.+?) typically eat (?P<x>.+)\.$"),
]
LIFESPAN_PATTERNS = [
    re.compile(r"^Longevity fact: the (?P<a>.+?) lives (?P<x>.+)\.$"),
    re.compile(r"^How long does an? (?P<a>.+?) live\? It (?:typically )?lives (?P<x>.+)\.$"),
]
TRAIT_PREFIXES = [
    "Size check: the ", "Here's a big one: the ", "Fact: the ",
    # A few lifespan rows carry a free-form predicate rather than "lives <x>";
    # they fall through to trait handling.
    "Longevity fact: the ",
]
# "How long does a X live? It <predicate>" with a non-"lives" predicate.
LIFESPAN_FALLBACK = re.compile(r"^How long does an? (?P<a>.+?) live\? It (?P<p>.+)\.$")
QUIZ_PREFIXES = ["Surprising but true: this animal ", "This animal "]

ADVERB_LEAD = re.compile(r"^(almost|mainly|mostly|primarily|largely|nearly|chiefly)\b", re.I)


def parse_fact(caption, animal):
    """Return (fact_type, core) for a fact caption, or (None, None)."""
    for pats, kind in ((HABITAT_PATTERNS, "habitat"),
                       (DIET_PATTERNS, "diet"),
                       (LIFESPAN_PATTERNS, "lifespan")):
        for pat in pats:
            m = pat.match(caption)
            if m:
                return kind, m.group("x")
    for pre in TRAIT_PREFIXES:
        if caption.startswith(pre):
            rest = caption[len(pre):]
            if rest.startswith(animal):
                return "trait", rest[len(animal):].lstrip().rstrip(".")
    m = LIFESPAN_FALLBACK.match(caption)
    if m:
        return "trait", m.group("p").rstrip(".")
    return None, None


def parse_quiz(overlay):
    for pre in QUIZ_PREFIXES:
        if overlay.startswith(pre):
            return overlay[len(pre):].strip().rstrip(".")
    return None


# --- template pools -------------------------------------------------------
HABITAT = [
    "The {a} lives in {x}.",
    "You'll find the {a} in {x}.",
    "The {a} makes its home in {x}.",
    "Where does the {a} live? In {x}.",
    "Look for the {a} in {x}.",
    "Home turf for the {a}: {x}.",
    "The {a} is at home in {x}.",
    "In the wild, the {a} lives in {x}.",
    "Native habitat of the {a}: {x}.",
    "The {a} is found in {x}.",
    "Habitat check — the {a} lives in {x}.",
    "Spot the {a} in {x}.",
    "The {a} sticks to {x}.",
    "Range of the {a}: {x}.",
]
DIET_VERB = [
    "The {a} feeds on {x}.",
    "The {a} eats {x}.",
    "The {a} dines on {x}.",
    "Feeding time — the {a} eats {x}.",
    "Diet check: the {a} feeds on {x}.",
]
DIET_NOUN = [
    "On the menu for the {a}: {x}.",
    "The {a}'s diet: {x}.",
    "Dinner for the {a}: {x}.",
    "What does the {a} eat? {xc}.",
    "Menu for the {a}: {x}.",
]
LIFESPAN = [
    "The {a} lives {x}.",
    "How long does {an} {a} live? {xc}.",
    "Lifespan of the {a}: {x}.",
    "The {a} can live {x}.",
    "Life expectancy for the {a}: {x}.",
    "The {a} typically lives {x}.",
    "Clock check — the {a} lives {x}.",
]
TRAIT = {
    VERB: [
        "The {a} {p}.",
        "Fact: the {a} {p}.",
        "Here's one — the {a} {p}.",
        "Did you know? The {a} {p}.",
        "Worth knowing: the {a} {p}.",
        "Nature fact — the {a} {p}.",
        "Get this: the {a} {p}.",
    ],
    NOUN: [
        "The {a} is {p}.",
        "Meet the {a}: {p}.",
        "The {a} — {p}.",
        "Fact: the {a} is {p}.",
        "Did you know? The {a} is {p}.",
        "Say hello to the {a}: {p}.",
        "Profile: the {a} is {p}.",
    ],
    HAS: [
        "The {a} has {p}.",
        "Fact: the {a} has {p}.",
        "Did you know? The {a} has {p}.",
        "The {a} carries {p}.",
    ],
    POSS: [
        "The {a}'s {p}.",
        "About the {a}: its {p}.",
        "Fact: the {a}'s {p}.",
        "Did you know? The {a}'s {p}.",
    ],
    OWN: [
        "In the {a}, {p}.",
        "About the {a}: {p}.",
        "{ac} fact — {p}.",
        "Here's one about the {a}: {p}.",
        "With the {a}, {p}.",
    ],
    WAS: [
        "The {a} was {p}.",
        "Fact: the {a} was {p}.",
    ],
}

QUIZ_CLUE = {
    VERB: "This animal {p}.",
    NOUN: "This animal is {p}.",
    HAS: "This animal has {p}.",
    POSS: "This animal's {p}.",
    OWN: "In this species, {p}.",
    WAS: "This animal was {p}.",
}
QUIZ_CLUE_ALT = {
    VERB: "Surprising but true: this animal {p}.",
    NOUN: "Surprising but true: this animal is {p}.",
    HAS: "Surprising but true: this animal has {p}.",
    POSS: "Surprising but true: this animal's {p}.",
    OWN: "Surprising but true: in this species, {p}.",
    WAS: "Surprising but true: this animal was {p}.",
}
ANSWER = {
    VERB: "\U0001f389 Answer: the {a}! It {p}.",
    NOUN: "\U0001f389 Answer: the {a}! It's {p}.",
    HAS: "\U0001f389 Answer: the {a}! It has {p}.",
    POSS: "\U0001f389 Answer: the {a}! Its {p}.",
    OWN: "\U0001f389 Answer: the {a}! In this species, {p}.",
    WAS: "\U0001f389 Answer: the {a}! It was {p}.",
}

CAPTION_TMPL = (
    "\U0001f43e GUESS THE ANIMAL \U0001f43e\n\n{clue}\n\n"
    "Drop your guess in the comments! Answer revealed below in an hour. \U0001f447"
)


def build(src_path, out_path):
    wb = openpyxl.load_workbook(src_path)
    ws = wb["Content Calendar"]
    counters = collections.Counter()
    report = {"unparsed": [], "shapes": collections.Counter(),
              "types": collections.Counter(), "templates": collections.Counter()}

    for row in ws.iter_rows(min_row=2):
        vals = [c.value for c in row]
        pid, animal, ptype = vals[C_ID], vals[C_ANIMAL], vals[C_TYPE]
        an = indefinite_article(animal)

        if ptype == "Text Post (Fact)":
            kind, core = parse_fact(vals[C_CAPTION], animal)
            if kind is None:
                report["unparsed"].append((pid, vals[C_CAPTION]))
                continue
            core = PREDICATE_OVERRIDES.get(pid, core)
            report["types"][kind] += 1

            if kind == "habitat":
                pool, key = HABITAT, "habitat"
            elif kind == "diet":
                pool = DIET_NOUN if ADVERB_LEAD.match(core) else DIET_VERB + DIET_NOUN
                key = "diet-noun" if ADVERB_LEAD.match(core) else "diet"
            elif kind == "lifespan":
                pool, key = LIFESPAN, "lifespan"
            else:
                shape, core = classify(core, pid)
                report["shapes"][shape] += 1
                pool, key = TRAIT[shape], f"trait-{shape}"

            tmpl = pool[counters[key] % len(pool)]
            counters[key] += 1
            report["templates"][tmpl] += 1
            text = tmpl.format(a=animal, ac=animal, an=an, x=core, xc=cap(core), p=core)
            row[C_CAPTION].value = fix_articles(text)

        else:  # Image Post (Quiz)
            core = parse_quiz(vals[C_OVERLAY])
            if core is None:
                report["unparsed"].append((pid, vals[C_OVERLAY]))
                continue
            core = PREDICATE_OVERRIDES.get(pid, core)
            shape, core = classify(core, pid)
            report["shapes"][shape] += 1
            report["types"]["quiz"] += 1

            counters["quiz"] += 1
            alt = counters["quiz"] % 3 == 0
            clue = (QUIZ_CLUE_ALT if alt else QUIZ_CLUE)[shape].format(p=core)
            clue = fix_articles(clue)
            answer = fix_articles(ANSWER[shape].format(a=animal, p=core))

            caption_clue = clue if alt else f"Clue: {clue}"
            row[C_OVERLAY].value = clue
            row[C_CAPTION].value = CAPTION_TMPL.format(clue=caption_clue)
            row[C_QUESTION].value = CAPTION_TMPL.format(clue=caption_clue)
            row[C_ANSWER].value = answer

    wb.save(out_path)
    return report


def main():
    src = sys.argv[1] if len(sys.argv) > 1 else "plan/wildnatureusa_6month_content_plan.xlsx"
    out = sys.argv[2] if len(sys.argv) > 2 else "plan/content_plan_fixed.xlsx"
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    rep = build(src, out)

    print(f"wrote {out}\n")
    print("by fact type:")
    for k, v in rep["types"].most_common():
        print(f"  {k:10} {v}")
    print("\npredicate shapes (trait + quiz):")
    for k, v in rep["shapes"].most_common():
        print(f"  {k:6} {v}")
    print(f"\ndistinct templates used: {len(rep['templates'])}")
    top = rep["templates"].most_common(3)
    print("busiest template:", top[0] if top else None)
    print(f"unparsed rows: {len(rep['unparsed'])}")
    for pid, txt in rep["unparsed"][:10]:
        print("  ", pid, txt[:90])


if __name__ == "__main__":
    main()
