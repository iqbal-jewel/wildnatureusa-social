"""Grammar repair for the WildNatureUSA content plan.

The source workbook builds captions by gluing a raw predicate onto a subject,
which only works when the predicate is verb-initial. Predicates that are noun
phrases ("a stocky, burrowing marsupial") or that carry their own subject
("the strike creates a flash") come out ungrammatical.

This module classifies a predicate into one of six shapes and exposes the
pieces the rewriter needs to rebuild a correct sentence around it.
"""
import re

# --- shapes --------------------------------------------------------------
VERB = "verb"   # verb-initial:      "can weigh over 1,000 pounds"  -> The X <p>
NOUN = "noun"   # predicate nominal: "a stocky marsupial"           -> The X is <p>
HAS = "has"     # bare body part:    "a wingspan over 7 feet"       -> The X has <p>
POSS = "poss"   # possessive-initial:"its heart alone can weigh..." -> The X's <rest>
OWN = "own"     # own subject:       "the strike creates a flash"   -> In the X, <p>
WAS = "was"     # past-tense nominal:"long thought extinct until.." -> The X was <p>

# Adverbs that can lead a predicate; strip, classify the remainder, re-attach.
ADVERBS = {
    "often", "sometimes", "rarely", "mostly", "constantly", "actually",
    "only", "closely", "brightly", "entirely", "highly", "long", "not",
    "about", "briefly", "quickly", "slowly", "usually", "typically",
}

# First words that begin a clause with its own subject.
OWN_SUBJECT_WORDS = {
    "males", "females", "both", "some", "each", "no", "chicks", "stripes",
    "songs", "quills", "troops", "conservationists", "different", "adults",
    "juveniles", "young", "pups",
}

# Finite verbs seen leading a predicate. Used both to classify and, in the
# validator, to catch a copula wrongly stacked on a verb ("is lays").
VERB_WORDS = {
    "absorbs", "accelerates", "acts", "adds", "appears", "beats", "breaches",
    "breathes", "breeds", "buries", "can", "cannot", "carries", "caches",
    "changes", "closes", "communicates", "comes", "contains", "creates",
    "curls", "delivers", "demonstrates", "detects", "digests", "digs",
    "distinguishes", "dives", "does", "dunks", "eats", "emerges", "enters",
    "extends", "farms", "feeds", "filters", "flaps", "flashes", "flies",
    "fuses", "gets", "gives", "glows", "goes", "grows", "guards", "has",
    "hatches", "helped", "helps", "holds", "hovers", "hums", "hunts",
    "includes", "inflates", "keeps", "kills", "lacks", "launches", "lays",
    "leaves", "lives", "loses", "makes", "mates", "migrates", "mimics",
    "minimizes", "moves", "navigates", "opens", "packs", "performs", "posts",
    "pretends", "produces", "protects", "pushes", "puffs", "reaches",
    "recycles", "regrows", "relies", "releases", "remembers", "resembles",
    "resolves", "returns", "reveals", "runs", "scavenges", "secretes",
    "senses", "shakes", "shares", "skips", "slaps", "sleeps", "slides",
    "smells", "spends", "spits", "sprays", "stands", "stores", "sunbathes",
    "swims", "switches", "takes", "tends", "travels", "undergoes",
    "urinates", "uses", "walks", "wanders", "warns", "weighs", "wiggles",
    "will", "work", "works", "would",
}

# First words that begin a predicate nominal / adjectival needing "is".
NOUN_WORDS = {
    "a", "an", "one", "among", "known", "able", "humans'", "smaller",
    "slender", "large", "bright", "solitary", "covered", "considered",
    "named", "marked", "built", "inspired", "native", "roughly",
}

# "a ..." predicates that actually carry their own subject.
_A_OWN = re.compile(r"^an? (single|female|male|broad|mucus|famous)\b", re.I)
# "the ..." predicates that are superlative descriptions, not clauses.
_THE_NOUN = re.compile(r"^the (only|last|second|most|few)\b|^the \w*est\b", re.I)

# A handful of predicates the general rules can't place. Keyed by Post_ID.
OVERRIDES = {
    "FB-TXT-0940": HAS,   # "highly dexterous front paws with sensitive touch"
    "FB-TXT-0954": HAS,   # "a wingspan that can stretch over 7 feet"
    "FB-TXT-0963": HAS,   # "a compact body built to conserve heat"
    "FB-TXT-0800": WAS,   # "long thought extinct until a specimen was found"
    "FB-TXT-0869": NOUN,  # "about the size of a house cat"
    "FB-IMG-0950": OWN,   # "in some species the tiny male fuses..."
    "FB-TXT-1079": OWN,   # "a queen can live 10 to 30 years..."
    "FB-TXT-1261": OWN,   # "individual colonies can live for decades"
}

# Predicates that need the text itself changed, not just a different frame.
# Here the source already fronts "in some species", which would double up
# against the OWN template's own "In this species," lead-in.
PREDICATE_OVERRIDES = {
    "FB-IMG-0950": "the tiny male fuses permanently to the much larger female",
}

# Vowel-initial words that take "a", and consonant-initial ones that take "an".
_A_EXCEPT = re.compile(r"^(eu|ewe|one|once|uni|use|usu|ubiq|uk)", re.I)
_AN_EXCEPT = re.compile(r"^(hour|honest|honou?r|heir)", re.I)


def indefinite_article(word: str) -> str:
    """Return 'a' or 'an' for the word that follows."""
    w = word.lstrip("\"'([").strip()
    if not w:
        return "a"
    if _AN_EXCEPT.match(w):
        return "an"
    if _A_EXCEPT.match(w):
        return "a"
    return "an" if w[0].lower() in "aeiou" else "a"


def fix_articles(text: str) -> str:
    """Repair a/an disagreement introduced by templating."""

    def _swap(m):
        art, nxt = m.group(1), m.group(2)
        correct = indefinite_article(nxt)
        if art[0].isupper():
            correct = correct.capitalize()
        return f"{correct} {nxt}"

    return re.sub(r"\b([Aa]n?) ([A-Za-z][\w'-]*)", _swap, text)


def classify(predicate: str, post_id: str = "") -> tuple:
    """Classify a predicate.

    Returns (shape, predicate) where predicate is normalised -- for POSS the
    leading "its " is stripped so callers can attach their own possessor.
    """
    p = predicate.strip()
    if not p:
        return VERB, p

    if post_id in OVERRIDES:
        shape = OVERRIDES[post_id]
        if shape == HAS:
            return HAS, p
        if shape == WAS:
            # drop the leading adverb; templates re-attach it after "was"
            return WAS, p
        return shape, p

    first = p.split()[0].lower().strip(",")

    # The source glues "is" onto some predicates that already carry their own
    # verb ("is lays the largest egg"). Drop it and re-classify: if what
    # follows really is a nominal, the NOUN templates put "is" back.
    if first in ("is", "are") and len(p.split()) > 1:
        rest = p[len(p.split()[0]):].lstrip()
        shape, norm = classify(rest, "")
        rest_first = rest.split()[0].lower().strip(",")
        if shape == VERB and rest_first not in VERB_WORDS:
            # "is" was a genuine copula before an adjective phrase we don't
            # otherwise recognise -- keep it.
            return NOUN, rest
        return shape, norm

    if first == "its":
        return POSS, p[4:].lstrip()

    if first in ADVERBS:
        rest = p[len(p.split()[0]):].lstrip()
        inner_shape, _ = classify(rest)
        if inner_shape in (NOUN, HAS):
            # "closely related to..." -> is closely related to...
            return NOUN, p
        if inner_shape == OWN:
            return OWN, p
        return VERB, p

    if first in OWN_SUBJECT_WORDS:
        return OWN, p

    if first == "the":
        return (NOUN, p) if _THE_NOUN.match(p) else (OWN, p)

    if first in ("a", "an"):
        return (OWN, p) if _A_OWN.match(p) else (NOUN, p)

    if first in NOUN_WORDS:
        return NOUN, p

    if first == "in":
        return OWN, p

    return VERB, p


def cap(text: str) -> str:
    """Capitalise the first letter without touching the rest."""
    return text[:1].upper() + text[1:] if text else text
