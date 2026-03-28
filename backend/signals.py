"""
Convergence signal detectors — feed Persuasion Score (Convergence 40%).

Research basis
──────────────
Prior implementation used content-word overlap and agreement-keyword counting,
which the cognitive science literature identifies as red herrings (Ireland &
Pennebaker 2010; Niederhoffer & Pennebaker 2002).  Content words track topic,
not cognitive alignment.

This rewrite is grounded in four validated signals:

1. **Function-Word Style Matching (LSM)** — Niederhoffer & Pennebaker (2002),
   Ireland & Pennebaker (2010), Gonzales et al. (2010).  Unconscious alignment
   on 8 function-word categories.  Strongest single predictor of coordination.

2. **Pronoun Convergence** — Gonzales et al. (2010).  Shift from I/you to
   we/our framing.  Strongest surface-level signal of group performance.

3. **Uptake & Building-On Ratio** — Dialogue act theory (Stolcke et al. 2000).
   Whether speakers engage with each other's contributions vs. talk past each
   other.

4. **Question-Type Arc** — Kept from prior implementation with refinements.
   Converging meetings show challenge→clarifying→confirmatory arc.

Composite weights (from literature review):
    LSM trajectory            35%
    Pronoun convergence       25%
    Uptake ratio              25%
    Question-type arc         15%

Transcript format (unchanged):
    [{"speaker": "speaker_0", "text": "...", "start": 12.4, "end": 15.8}, ...]
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal


# ---------------------------------------------------------------------------
# Shared types
# ---------------------------------------------------------------------------

@dataclass
class SignalResult:
    """Return type for all signal detectors."""
    signal: str
    converging: bool          # True = this signal says persuasion is succeeding
    score: float              # 0.0 – 1.0 continuous confidence
    evidence: list[str]       # Human-readable evidence strings
    details: dict             # Raw counts / intermediate values


# ---------------------------------------------------------------------------
# Function-word categories (Niederhoffer & Pennebaker 2002, 9 LIWC classes)
# ---------------------------------------------------------------------------

FUNCTION_WORD_CATEGORIES: dict[str, frozenset[str]] = {
    "articles": frozenset({"a", "an", "the"}),
    "prepositions": frozenset({
        "to", "with", "for", "in", "on", "at", "from", "by", "about",
        "into", "through", "during", "before", "after", "between", "over",
        "under", "against", "among", "toward", "towards", "upon",
    }),
    "personal_pronouns": frozenset({
        "i", "me", "my", "mine", "we", "us", "our", "ours",
        "you", "your", "yours", "he", "she", "him", "her", "his",
        "hers", "they", "them", "their", "theirs", "myself", "yourself",
        "ourselves",
    }),
    "impersonal_pronouns": frozenset({
        "it", "its", "that", "this", "these", "those",
        "anything", "everything", "something", "nothing",
        "anyone", "everyone", "someone",
    }),
    "auxiliary_verbs": frozenset({
        "is", "am", "are", "was", "were", "be", "been", "being",
        "have", "has", "had", "do", "does", "did",
        "will", "would", "shall", "should", "may", "might",
        "can", "could", "must",
    }),
    "conjunctions": frozenset({
        "and", "but", "or", "nor", "for", "yet", "so",
        "because", "since", "although", "while", "if", "unless", "until",
        "whether", "though",
    }),
    "negations": frozenset({
        "no", "not", "never", "neither", "nobody", "nothing",
        "nowhere", "dont", "doesnt", "didnt", "wont", "cant",
        "shouldnt", "wouldnt", "couldnt", "isnt", "arent", "wasnt",
    }),
    "quantifiers": frozenset({
        "few", "many", "much", "more", "most", "less", "least",
        "several", "some", "any", "all", "every", "each", "enough",
        "both", "half",
    }),
}


def _tokenize(text: str) -> list[str]:
    """Lowercase word tokenization, strips contractions."""
    text = text.lower().replace("'", "").replace("'", "").replace("n't", "nt")
    return re.findall(r"[a-z]+", text)


# ---------------------------------------------------------------------------
# Signal 1: Language Style Matching (LSM)
# ---------------------------------------------------------------------------

def _category_rate(words: list[str], category: frozenset[str]) -> float:
    if not words:
        return 0.0
    return sum(1 for w in words if w in category) / len(words)


def _compute_lsm(words_a: list[str], words_b: list[str]) -> float:
    """
    Compute LSM between two word lists across all function-word categories.

    LSM_cat = 1 - |rate_a - rate_b| / (rate_a + rate_b + 0.0001)
    Overall = mean of all category scores. Range [0, 1].
    """
    if not words_a or not words_b:
        return 0.0
    scores = []
    for cat_words in FUNCTION_WORD_CATEGORIES.values():
        rate_a = _category_rate(words_a, cat_words)
        rate_b = _category_rate(words_b, cat_words)
        score = 1.0 - abs(rate_a - rate_b) / (rate_a + rate_b + 0.0001)
        scores.append(score)
    return sum(scores) / len(scores) if scores else 0.0


def language_style_matching(
    utterances: list[dict],
    user_speaker: str,
) -> SignalResult:
    """
    Measure function-word style matching between user and audience.

    Computes LSM on first-half and second-half of conversation to get trajectory.
    Rising LSM = converging. Final score blends endpoint (60%) + trajectory (40%).
    """
    user_words_all: list[str] = []
    audience_words_all: list[str] = []
    for u in utterances:
        tokens = _tokenize(u["text"])
        if u["speaker"] == user_speaker:
            user_words_all.extend(tokens)
        else:
            audience_words_all.extend(tokens)

    if len(user_words_all) < 20 or len(audience_words_all) < 20:
        return SignalResult(
            signal="language_style_matching",
            converging=False,
            score=0.0,
            evidence=["Insufficient data for LSM (need 20+ words per side)"],
            details={"user_words": len(user_words_all), "audience_words": len(audience_words_all)},
        )

    # Split by utterance position for trajectory
    midpoint = len(utterances) // 2
    first_half = utterances[:midpoint]
    second_half = utterances[midpoint:]

    def collect_words(utts: list[dict], speaker_match: bool) -> list[str]:
        words: list[str] = []
        for u in utts:
            is_user = u["speaker"] == user_speaker
            if is_user == speaker_match:
                words.extend(_tokenize(u["text"]))
        return words

    user_first = collect_words(first_half, True)
    audience_first = collect_words(first_half, False)
    user_second = collect_words(second_half, True)
    audience_second = collect_words(second_half, False)

    lsm_first = _compute_lsm(user_first, audience_first)
    lsm_second = _compute_lsm(user_second, audience_second)
    lsm_overall = _compute_lsm(user_words_all, audience_words_all)
    trajectory = lsm_second - lsm_first

    # Score: blend endpoint (60%) + trajectory (40%)
    # Normalize LSM to 0-1 score: 0.7 maps to ~0.0, 0.9+ maps to ~1.0
    endpoint_score = max(0.0, min(1.0, (lsm_overall - 0.70) / 0.20))
    # Trajectory: +0.05 = full boost, -0.05 = full penalty
    trajectory_score = max(0.0, min(1.0, 0.5 + trajectory * 10))
    score = endpoint_score * 0.60 + trajectory_score * 0.40

    converging = score >= 0.35

    evidence = [
        f"Overall LSM: {lsm_overall:.3f}",
        f"First-half LSM: {lsm_first:.3f} → Second-half LSM: {lsm_second:.3f}",
        f"Trajectory: {trajectory:+.3f} ({'rising' if trajectory > 0.01 else 'falling' if trajectory < -0.01 else 'stable'})",
        f"Score: endpoint={endpoint_score:.2f} trajectory={trajectory_score:.2f} combined={score:.2f}",
    ]
    if converging:
        evidence.append("CONVERGING: function-word alignment detected")
    else:
        evidence.append("NOT CONVERGING: speakers in different cognitive modes")

    return SignalResult(
        signal="language_style_matching",
        converging=converging,
        score=round(score, 4),
        evidence=evidence,
        details={
            "lsm_overall": round(lsm_overall, 4),
            "lsm_first_half": round(lsm_first, 4),
            "lsm_second_half": round(lsm_second, 4),
            "trajectory": round(trajectory, 4),
        },
    )


# ---------------------------------------------------------------------------
# Signal 2: Pronoun convergence (we/our vs I/you)
# ---------------------------------------------------------------------------

_INCLUSIVE = frozenset({"we", "us", "our", "ours", "lets", "together", "ourselves"})
_EXCLUSIVE = frozenset({"i", "me", "my", "mine", "you", "your", "yours", "myself", "yourself"})


def pronoun_convergence(
    utterances: list[dict],
    user_speaker: str,
) -> SignalResult:
    """
    Track shift from I/you framing to we/our framing over the conversation.

    Gonzales et al. (2010): pronoun convergence toward "we" is the single
    strongest surface-level signal of group performance.
    """
    if len(utterances) < 6:
        return SignalResult(
            signal="pronoun_convergence",
            converging=False,
            score=0.0,
            evidence=["Insufficient utterances for pronoun analysis"],
            details={},
        )

    # Split into thirds for trajectory
    third = max(1, len(utterances) // 3)
    first_third = utterances[:third]
    final_third = utterances[-third:]

    def we_ratio(utts: list[dict]) -> float:
        inc = 0
        exc = 0
        for u in utts:
            for w in _tokenize(u["text"]):
                if w in _INCLUSIVE:
                    inc += 1
                elif w in _EXCLUSIVE:
                    exc += 1
        total = inc + exc
        return inc / total if total > 0 else 0.5

    ratio_first = we_ratio(first_third)
    ratio_final = we_ratio(final_third)
    ratio_all = we_ratio(utterances)
    shift = ratio_final - ratio_first

    # Score: final-third ratio (60%) + shift trajectory (40%)
    # ratio > 0.6 in final third = strong signal
    endpoint_score = max(0.0, min(1.0, (ratio_final - 0.2) / 0.5))
    # shift > 0.15 = convergence trajectory
    shift_score = max(0.0, min(1.0, 0.5 + shift * 3.0))
    score = endpoint_score * 0.60 + shift_score * 0.40

    converging = score >= 0.35

    evidence = [
        f"We/our ratio — first third: {ratio_first:.2f}, final third: {ratio_final:.2f}",
        f"Shift: {shift:+.2f} ({'toward we/our' if shift > 0.05 else 'toward I/you' if shift < -0.05 else 'stable'})",
        f"Overall we/our ratio: {ratio_all:.2f}",
    ]
    if converging:
        evidence.append("CONVERGING: speakers shifting to shared framing")
    else:
        evidence.append("NOT CONVERGING: speakers maintaining individual framing")

    return SignalResult(
        signal="pronoun_convergence",
        converging=converging,
        score=round(score, 4),
        evidence=evidence,
        details={
            "we_ratio_first_third": round(ratio_first, 4),
            "we_ratio_final_third": round(ratio_final, 4),
            "we_ratio_overall": round(ratio_all, 4),
            "shift": round(shift, 4),
        },
    )


# ---------------------------------------------------------------------------
# Signal 3: Uptake & building-on ratio
# ---------------------------------------------------------------------------

_UPTAKE_PHRASES = [
    "building on", "to add to", "yes and", "and also", "expanding on",
    "along those lines", "that connects", "that reminds me",
    "good point", "exactly right", "right and",
    "to your point", "as you said", "like you mentioned",
    "so what youre saying", "if i understand", "in other words",
    "what if we", "could we combine", "how about we", "lets try",
    "i agree", "agreed", "absolutely", "precisely",
    "that makes sense", "works for me", "sounds right",
    "great idea", "love that", "thats a plan", "im on board",
    "yes lets", "lets do", "lets go", "lets move", "moving forward",
    "were aligned", "were agreed", "im convinced", "im sold",
]

_RESISTANCE_PHRASES = [
    "but ", "however ", "on the other hand", "i disagree",
    "that wont work", "the problem with that", "i dont think",
    "my concern is", "with respect", "i hear you but",
    "that said", "the issue is", "let me push back",
    "im not sure", "im not convinced", "i have concerns",
    "thats not going to", "we cant just", "thats risky",
]


def uptake_ratio(
    utterances: list[dict],
    user_speaker: str,
) -> SignalResult:
    """
    Measure whether speakers engage with each other's contributions
    (building-on, agreement) vs. resist/talk past (but-prefacing, restating).

    Includes trajectory: rising uptake in the final third is the strongest signal.
    """
    if len(utterances) < 4:
        return SignalResult(
            signal="uptake_ratio",
            converging=False,
            score=0.0,
            evidence=["Insufficient utterances for uptake analysis"],
            details={},
        )

    def classify_utterances(utts: list[dict]) -> tuple[int, int]:
        uptake = 0
        resistance = 0
        for u in utts:
            text = _tokenize_text_for_phrases(u["text"])
            is_uptake = any(text.startswith(p) or (", " + p) in text or (". " + p) in text for p in _UPTAKE_PHRASES)
            is_resist = any(text.startswith(p) or (", " + p) in text or (". " + p) in text for p in _RESISTANCE_PHRASES)
            if is_uptake:
                uptake += 1
            if is_resist:
                resistance += 1
        return uptake, resistance

    uptake_all, resist_all = classify_utterances(utterances)
    total_signals = uptake_all + resist_all

    # Trajectory: first half vs second half
    mid = len(utterances) // 2
    uptake_first, resist_first = classify_utterances(utterances[:mid])
    uptake_second, resist_second = classify_utterances(utterances[mid:])

    def ratio(u: int, r: int) -> float:
        t = u + r
        return u / t if t > 0 else 0.5

    ratio_all = ratio(uptake_all, resist_all)
    ratio_first = ratio(uptake_first, resist_first)
    ratio_second = ratio(uptake_second, resist_second)
    trajectory = ratio_second - ratio_first

    # Also factor in overall uptake density (uptake hits per utterance)
    density = uptake_all / len(utterances) if utterances else 0.0

    # Score: ratio (50%) + trajectory (25%) + density (25%)
    ratio_score = max(0.0, min(1.0, ratio_all))
    traj_score = max(0.0, min(1.0, 0.5 + trajectory * 2.5))
    density_score = max(0.0, min(1.0, density / 0.25))
    score = ratio_score * 0.50 + traj_score * 0.25 + density_score * 0.25

    converging = score >= 0.35

    evidence = [
        f"Uptake markers: {uptake_all}, Resistance markers: {resist_all}",
        f"Uptake ratio: {ratio_all:.2f} (first half: {ratio_first:.2f}, second half: {ratio_second:.2f})",
        f"Trajectory: {trajectory:+.2f}",
        f"Uptake density: {density:.2f} per utterance",
    ]
    if converging:
        evidence.append("CONVERGING: speakers building on each other's contributions")
    else:
        evidence.append("NOT CONVERGING: resistance or disengagement pattern")

    return SignalResult(
        signal="uptake_ratio",
        converging=converging,
        score=round(score, 4),
        evidence=evidence,
        details={
            "uptake_count": uptake_all,
            "resistance_count": resist_all,
            "ratio_overall": round(ratio_all, 4),
            "ratio_first_half": round(ratio_first, 4),
            "ratio_second_half": round(ratio_second, 4),
            "trajectory": round(trajectory, 4),
            "density": round(density, 4),
            "total_questions": total_signals,  # kept for ego_safety compat
            "total_challenging": resist_all,
        },
    )


def _tokenize_text_for_phrases(text: str) -> str:
    """Normalize text for phrase matching: lowercase, strip punctuation."""
    return text.lower().replace("'", "").replace("'", "").replace("n't", "nt").strip()


# ---------------------------------------------------------------------------
# Signal 4: Question-type arc (refined from prior implementation)
# ---------------------------------------------------------------------------

# Phrases that signal a challenging (skeptical/adversarial) question
_CHALLENGING_PATTERNS = [
    r"\bwhy (should|would|do|don't|can't|won't)\b",
    r"\bwhat (evidence|proof|data|guarantee|assurance)\b",
    r"\bhow (do you know|can you be sure|can you guarantee)\b",
    r"\bisn't (that|this|it)\b",
    r"\baren't (we|you)\b",
    r"\bwhat makes you (think|believe|say)\b",
    r"\bwhat's (the|your) (roi|risk|downside|cost|catch)\b",
    r"\bbut (what|how|why) (if|about|happens|would)\b",
    r"\bhave you (considered|thought about|looked at)\b",
    r"\bwhat (about|if) (the|we|they|it)\b",
    r"\bcould (this|that|it) (fail|backfire|go wrong)\b",
    r"\bwhat happens (if|when)\b",
]

# Phrases that signal a clarifying (constructive/engaged) question
_CLARIFYING_PATTERNS = [
    r"\bwhat do you mean\b",
    r"\bcan you (explain|clarify|elaborate|walk me through)\b",
    r"\bhow (would|will|does|do|could) (that|this|we|it)\b",
    r"\btell me more\b",
    r"\bwhat does that look like\b",
    r"\bcan you give (me|us) an example\b",
    r"\bwhat (specifically|exactly)\b",
    r"\bhow long (would|will|does|do)\b",
    r"\bwhat (resources|support|help) (do we|would we|will we)\b",
]

# Phrases that signal confirmatory/implementation-oriented questions
_CONFIRMATORY_PATTERNS = [
    r"\bdoes .{0,40}? sound (right|good|fair|reasonable|okay)\b",
    r"\bwhen (would|will|could|can|should) (we|you|this)\b",
    r"\bwho (would|will|should|is) (be )?(dri|responsible|owning|leading|doing)\b",
    r"\bwhat'?s (the|our) (next step|timeline|schedule|plan|deadline)\b",
    r"\bwhen (do|can|should) we (start|kick off|begin|meet|schedule)\b",
    r"\bhow (do|should|can) we (schedule|coordinate|organize|split|assign)\b",
    r"\bwho (else|should|can|wants to) (join|help|be involved|weigh in)\b",
    r"\bshould (we|i|this) be (dri|owner|responsible)\b",
    r"\bcan (we|i|you) (schedule|set up|book|confirm)\b",
    r"\bdo we (need|want|have) (a|an|the)?\s*(meeting|session|sync|time)\b",
    r"\b(is that|are we|is everyone) (good|aligned|okay|set|ready)\b",
]

_RE_CHALLENGING = [re.compile(p, re.IGNORECASE) for p in _CHALLENGING_PATTERNS]
_RE_CLARIFYING = [re.compile(p, re.IGNORECASE) for p in _CLARIFYING_PATTERNS]
_RE_CONFIRMATORY = [re.compile(p, re.IGNORECASE) for p in _CONFIRMATORY_PATTERNS]
_RE_QUESTION = re.compile(r"\?|^(who|what|when|where|why|how|is|are|was|were|will|would|could|should|can|do|does|did|have|has|had)\b", re.IGNORECASE)


def _classify_question(text: str) -> Literal["challenging", "clarifying", "confirmatory", "neutral", "not_question"]:
    if not _RE_QUESTION.search(text):
        return "not_question"
    challenging_hits = sum(1 for p in _RE_CHALLENGING if p.search(text))
    clarifying_hits = sum(1 for p in _RE_CLARIFYING if p.search(text))
    confirmatory_hits = sum(1 for p in _RE_CONFIRMATORY if p.search(text))
    max_hits = max(challenging_hits, clarifying_hits, confirmatory_hits)
    if max_hits == 0:
        return "neutral"
    if challenging_hits == max_hits:
        return "challenging"
    if confirmatory_hits == max_hits:
        return "confirmatory"
    if clarifying_hits == max_hits:
        return "clarifying"
    return "neutral"


def question_type_arc(
    utterances: list[dict],
    user_speaker: str,
) -> SignalResult:
    """
    Measure whether audience questions arc toward convergence.

    Path A: challenge ratio drops (adversarial → constructive)
    Path B: zero adversarial + confirmatory presence rises
    """
    audience_questions = [
        {**u, "_qtype": _classify_question(u["text"])}
        for u in utterances
        if u["speaker"] != user_speaker and _classify_question(u["text"]) != "not_question"
    ]

    if len(audience_questions) < 3:
        return SignalResult(
            signal="question_type_arc",
            converging=False,
            score=0.5,  # neutral, not zero — don't penalize short meetings
            evidence=[f"Insufficient questions ({len(audience_questions)} < 3 required)"],
            details={"total_questions": len(audience_questions), "total_challenging": 0},
        )

    midpoint = len(audience_questions) // 2
    first_half = audience_questions[:midpoint]
    second_half = audience_questions[midpoint:]

    def count_types(qs: list[dict]) -> dict:
        counts = {"challenging": 0, "clarifying": 0, "confirmatory": 0, "neutral": 0}
        for q in qs:
            counts[q["_qtype"]] += 1
        return counts

    counts_first = count_types(first_half)
    counts_second = count_types(second_half)

    def ratio(counts: dict, key: str) -> float:
        total = sum(counts.values())
        return counts[key] / total if total else 0.0

    challenge_ratio_first = ratio(counts_first, "challenging")
    challenge_ratio_second = ratio(counts_second, "challenging")
    confirm_ratio_first = ratio(counts_first, "confirmatory")
    confirm_ratio_second = ratio(counts_second, "confirmatory")
    challenge_delta = challenge_ratio_second - challenge_ratio_first
    confirm_delta = confirm_ratio_second - confirm_ratio_first

    total_challenging = sum(1 for q in audience_questions if q["_qtype"] == "challenging")
    total_confirmatory = sum(1 for q in audience_questions if q["_qtype"] == "confirmatory")

    path_a = (
        challenge_delta <= -0.10
        or (challenge_ratio_first > 0 and challenge_ratio_second == 0)
        or (challenge_ratio_second <= 0.25 and challenge_ratio_first >= 0.40)
    )
    path_b = (
        total_challenging == 0
        and total_confirmatory >= 1
        and confirm_ratio_second > confirm_ratio_first
    )

    converging = path_a or path_b

    # Score
    challenge_score = min(1.0, max(0.0, 0.5 - challenge_delta))
    confirm_score = min(1.0, confirm_ratio_second * 2)
    if total_challenging == 0:
        score = max(challenge_score * 0.3 + confirm_score * 0.7, 0.5 if path_b else 0.4)
    else:
        score = challenge_score

    evidence = [
        f"First-half: {counts_first} (challenge {challenge_ratio_first:.0%}, confirm {confirm_ratio_first:.0%})",
        f"Second-half: {counts_second} (challenge {challenge_ratio_second:.0%}, confirm {confirm_ratio_second:.0%})",
        f"Challenge delta: {challenge_delta:+.0%} | Confirmatory delta: {confirm_delta:+.0%}",
    ]
    if converging:
        path_label = "Path A (challenge drop)" if path_a else "Path B (collaborative → confirmatory)"
        evidence.append(f"CONVERGING: {path_label}")
    else:
        evidence.append("NOT CONVERGING: question tone unchanged or worsening")

    return SignalResult(
        signal="question_type_arc",
        converging=converging,
        score=round(score, 4),
        evidence=evidence,
        details={
            "total_questions": len(audience_questions),
            "total_challenging": total_challenging,
            "total_confirmatory": total_confirmatory,
            "challenge_ratio_first": challenge_ratio_first,
            "challenge_ratio_second": challenge_ratio_second,
            "path_a": path_a,
            "path_b": path_b,
        },
    )


# ---------------------------------------------------------------------------
# Combined convergence signal
# ---------------------------------------------------------------------------

def convergence_score(
    utterances: list[dict],
    user_speaker: str,
) -> tuple[float, list[SignalResult]]:
    """
    Run all four signals and return a combined convergence score 0.0–1.0.

    Weights (research-derived):
        language_style_matching  35%
        pronoun_convergence      25%
        uptake_ratio             25%
        question_type_arc        15%
    """
    results = [
        language_style_matching(utterances, user_speaker),
        pronoun_convergence(utterances, user_speaker),
        uptake_ratio(utterances, user_speaker),
        question_type_arc(utterances, user_speaker),
    ]
    weights = [0.35, 0.25, 0.25, 0.15]
    combined = sum(r.score * w for r, w in zip(results, weights))
    return round(combined, 4), results
