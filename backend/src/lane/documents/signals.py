"""Cheap, deterministic signals that check the classifier.

Keyword patterns that are characteristic of each document type across carriers and states
(industry vocabulary, not anything from one sample). They need a text layer, so scans and
photos get no keyword vote. Filenames get their own, weaker hint.
"""

import re
from dataclasses import dataclass

from lane.documents.types import DocType

# (pattern, weight). Patterns are case-insensitive.
KEYWORDS: dict[DocType, list[tuple[str, float]]] = {
    DocType.APPLICATION: [
        (r"insurance application", 3),
        (r"fraud statement|fraudulent insurance act", 2),
        (r"description of operation", 1.5),
        (r"signature of (the )?(insured|applicant)", 1.5),
        (r"\bFEIN\b|tax id", 1),
        (r"years of experience", 0.5),
    ],
    DocType.QUOTE_FORM: [
        (r"quick quote|quote request|indication request", 3),
        (r"prior policy dates|prior carrier", 1.5),
        (r"radius info", 1),
        (r"commodities transported", 1),
    ],
    DocType.DRIVER_SCHEDULE: [
        (r"\bdriver (list|schedule|#)", 2),
        (r"date of birth|\bDOB\b", 1),
        (r"(driver )?licen[sc]e ?#|state licensed|CDL number", 1),
        (r"date of hire|\bD\.?O\.?H\b|hire date", 1),
    ],
    DocType.VEHICLE_SCHEDULE: [
        (r"vehicle (list|schedule|#)|fleet (list|schedule)|unit #", 2),
        (r"\bVIN\b", 1.5),
        (r"\bmake\b", 0.5),
        (r"cost new|stated value|\bTIV\b", 1),
    ],
    DocType.MVR: [
        (r"driver record|motor vehicle record|\bMVR\b", 3),
        (r"violations/?convictions|failures to appear", 2),
        (r"suspensions/?revocations", 2),
        (r"license (and|&) permit information", 1.5),
    ],
    DocType.DRIVERS_LICENSE: [
        (r"driver'?s? licen[sc]e|commercial driver licen[sc]e", 3),
        (r"\bDOB\b", 1),
        (r"\bRESTR?\b|\bRESTRICTIONS?\b", 1),
        (r"\bEND\b|\bENDORSEMENTS?\b", 0.5),
        (r"\bEXP\b|\bISS\b", 0.5),
        (r"\bHGT\b|\bEYES\b|\bSEX\b", 0.5),
    ],
    DocType.LOSS_RUN: [
        (r"total incurred|total reserved|losses paid", 3),
        (r"claim number|claim status|date of loss", 2),
        (r"run date|valued as of|valuation date", 1.5),
        (r"policy period", 1),
    ],
    DocType.LOSS_HISTORY_LETTER: [
        (r"to whom it may concern", 2),
        (r"no (paid )?losses|no claims (have been )?(reported|filed)", 3),
        (r"loss history", 1),
    ],
    DocType.DRIVER_EXCLUSION: [
        (r"(named )?driver exclusion|exclusion of (a )?named driver", 4),
        (r"\bexcluded driver\b|\bis (hereby )?excluded\b|\bto be excluded\b", 3),
        (r"will not (operate|drive)|shall not (operate|drive)", 2),
    ],
    DocType.IFTA_RETURN: [
        (r"\bIFTA\b", 3),
        (r"jurisdiction summary|by jurisdiction", 2),
        (r"taxable (miles|gal)|tax paid gal|net taxable", 2),
        (r"fuel tax|carrier tax", 1),
    ],
}

FILENAME_HINTS: list[tuple[str, DocType]] = [
    (r"\bMVRs?\b|motor vehicle", DocType.MVR),
    (r"\bIFTA\b|\bQ[1-4][ _-]?(20)?\d{2}\b", DocType.IFTA_RETURN),
    (r"loss ?runs?|\b(AL|APD|MTC|GL|PD)[ _-]?\d{2}[ _-]\d{2}\b", DocType.LOSS_RUN),
    (r"quote", DocType.QUOTE_FORM),
    (r"\bapp(lication)?\b|\bACORD\b", DocType.APPLICATION),
    (r"driver", DocType.DRIVER_SCHEDULE),
    (r"vehicle|fleet|\bunits?\b|equipment", DocType.VEHICLE_SCHEDULE),
    (r"licen[sc]e|\bDL\b|\bCDL\b", DocType.DRIVERS_LICENSE),
]

MIN_SCORE = 3.0  # a keyword vote needs at least this much evidence


@dataclass(frozen=True)
class KeywordVote:
    doc_type: DocType | None
    score: float
    runner_up: DocType | None
    scores: dict[str, float]


def keyword_vote(text: str) -> KeywordVote:
    scores: dict[DocType, float] = {}
    for doc_type, patterns in KEYWORDS.items():
        total = sum(w for p, w in patterns if re.search(p, text, re.IGNORECASE))
        if total:
            scores[doc_type] = total
    ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
    top = ranked[0] if ranked else None
    winner = top[0] if top and top[1] >= MIN_SCORE else None
    runner_up = ranked[1][0] if len(ranked) > 1 else None
    return KeywordVote(
        doc_type=winner,
        score=top[1] if top else 0.0,
        runner_up=runner_up,
        scores={k.value: v for k, v in ranked},
    )


def filename_hint(filename: str) -> DocType | None:
    stem = re.sub(r"\.[A-Za-z0-9]{2,5}$", "", filename)
    stem = re.sub(r"[_]+", " ", stem)  # underscores separate words in filenames
    stem = re.sub(r"(?<=\d)(?=Q[1-4]\b)|(?<=[A-Za-z])(?=\d{4}\b)", " ", stem)
    for pattern, doc_type in FILENAME_HINTS:
        if re.search(pattern, stem, re.IGNORECASE):
            return doc_type
    return None
