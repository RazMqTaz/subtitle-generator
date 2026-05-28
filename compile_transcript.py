import json
from dataclasses import dataclass
from pathlib import Path

MAX_CHARS_PER_LINE = 42
MAX_LINES = 2
MAX_CHARS_TOTAL = 84
MIN_DURATION_MS = 833
MAX_DURATION_MS = 7000
PAUSE_FOR_BREAK = 500
MAX_CPS = 17
MIN_GAP_MS = 83
LEAD_IN_MS = 100
LEAD_OUT_MS = 300
SENTENCE_ENDS = {".", "?", "!"}
CLAUSE_ENDS = {",", ";", ":"}
ABBREVIATIONS = {
    "mr.",
    "mrs.",
    "dr.",
    "ms.",
    "st.",
    "jr.",
    "sr.",
    "e.g.",
    "i.e.",
    "etc.",
    "u.s.",
    "u.k.",
}


@dataclass
class Word:
    text: str
    start: int
    end: int


@dataclass
class Cue:
    text: str
    start: int
    end: int


def compile_transcript(input_path: Path) -> list[Cue]:
    """
    Orchestrates compile_transcript.py.
    """
    print("Compiling transcript...")
    words = list_words(path=input_path)
    sentences = segment_sentences(words=words)
    cues = group_into_cues(sentences=sentences)
    cues = split_cue(cues=cues)
    cues = cleanup_timings(cues=cues)
    print("Transcript compiled!")
    return cues


def list_words(path: Path) -> list[Word]:
    """
    Read transcript JSON, merge sub-word tokens into whole words.
    """
    with open(file=path, encoding="utf-8") as f:
        transcript = json.load(f)

    tokens = transcript["tokens"]
    if not tokens:
        return []
    print("Compiling words...")
    words: list[Word] = []
    first = tokens[0]
    current_word: str = first["text"]
    start_ms = first["start_ms"]
    end_ms = first["end_ms"]

    for token in tokens[1:]:

        text = token["text"]

        # new word, starts with a ' ' -> send current Word
        if text.startswith(" "):
            words.append(Word(text=current_word, start=start_ms, end=end_ms))
            current_word = text
            start_ms = token["start_ms"]
            end_ms = token["end_ms"]
        # same word, or first word in transcript ->
        # concatonate text, updated end_ms, but only update start_ms if there is none
        else:
            current_word += text
            end_ms = token["end_ms"]

    # send final word
    words.append(Word(text=current_word, start=start_ms, end=end_ms))
    print(f"Successfully compiled {len(words)} words!")
    return words


def segment_sentences(words: list[Word]) -> list[list[Word]]:
    """
    Split words list into sentences using sentence-end punctuation
    + abbreviation guard. Pauses are handled by Soniox automatically
    (detects pauses and adds punctuation)
    """
    sentences: list[list[Word]] = []
    sentence: list[Word] = []
    print("Segmenting sentences...")
    for word in words:
        sentence.append(word)

        # if word contains punctuation that is NOT Mr., Dr., etc... -> ship sentence and clear
        text = word.text
        if (
            text.rstrip()[-1] in SENTENCE_ENDS
            and text.strip().lower() not in ABBREVIATIONS
        ):
            sentences.append(sentence)
            sentence = []
    if sentence:
        sentences.append(sentence)
    print(f"Successfully segmented {len(sentences)} sentences!")
    return sentences


def min_readable_duration_ms(chars: int) -> int:
    """
    Returns minimum ms a cue needs to be on screen to be readable.
    """
    return chars * 1000 // MAX_CPS


def cost_function(cue: list[Word]) -> float:
    """
    Defines the cost of bad cues. Can be adjusted to adjust results
    """
    cost = 0.0
    # Soft penalties (higher = worse)
    ORPHAN_PENALTY = 50  # per missing word below MIN_WORDS_PER_CUE
    SHORT_DURATION_PENALTY = 30  # flat, if duration < MIN_DURATION
    CPS_OVERSHOOT_PENALTY = 0.01  # per ms of reading speed shortfall
    NO_BOUNDARY_PENALTY = 20  # if cue doesn't end at .,;:?!
    IMBALANCE_PENALTY = 0.5  # per char away from ideal length

    # Bonuses (lower = better)
    SENTENCE_END_BONUS = -30  # if cue ends at .?!

    # Constants
    MIN_WORDS_PER_CUE = 3
    IDEAL_CHARS = 60

    num_words = len(cue)
    num_chars = sum(len(word.text) for word in cue)
    cue_time = cue[-1].end - cue[0].start

    if num_chars > MAX_CHARS_TOTAL:
        return float("inf")
    if cue_time > MAX_DURATION_MS:
        return float("inf")

    cost += IMBALANCE_PENALTY * abs(IDEAL_CHARS - num_chars)

    if num_words < MIN_WORDS_PER_CUE:
        cost += ORPHAN_PENALTY * (MIN_WORDS_PER_CUE - num_words)
    if cue_time < MIN_DURATION_MS:
        cost += SHORT_DURATION_PENALTY
    if cue_time < min_readable_duration_ms(num_chars):
        cost += CPS_OVERSHOOT_PENALTY * (min_readable_duration_ms(num_chars) - cue_time)

    last_word_text = cue[-1].text.rstrip()
    if last_word_text.endswith(tuple(SENTENCE_ENDS)):
        cost += SENTENCE_END_BONUS
    elif last_word_text.endswith(tuple(CLAUSE_ENDS)):
        pass  # Neither good nor bad, can be adjusted
    else:
        cost += NO_BOUNDARY_PENALTY
    return cost


def partition_sentence(sentence: list[Word]) -> list[list[Word]]:
    """
    Uses cost function to find optimal cue partition, dynamic programming solution.
    """
    partition: list[list[Word]] = []
    length = len(sentence)
    # partitioning zero words costs nothing, initialize each index after to infinity
    dp: list[float] = [0.0] + [float("inf")] * length

    parent: list[int] = [0] * (length + 1)
    for i in range(1, length + 1):
        for k in range(0, i):
            candidate = dp[k] + cost_function(sentence[k:i])
            if candidate < dp[i]:
                dp[i] = candidate
                parent[i] = k

    """
    Now construct partition knowing the best location and return.
    """
    i = length
    while i > 0:
        cue = sentence[parent[i] : i]
        partition.append(cue)
        i = parent[i]
    partition.reverse()
    return partition


def group_into_cues(sentences: list[list[Word]]) -> list[Cue]:
    """
    Convert list of sentences into flat list of Cues.
    Partitioning each sentence optimally via partition_sentence.
    """
    print(f"Grouping partitioned sentences into cues...")
    cues: list[Cue] = []
    for sentence in sentences:
        partitions = partition_sentence(sentence=sentence)
        for partition in partitions:
            cue = Cue(
                text="".join(w.text for w in partition).lstrip(),
                start=partition[0].start,
                end=partition[-1].end,
            )
            cues.append(cue)

    print(f"Successfully grouped {len(cues)} cues!")
    return cues


def try_clause_break(text: str) -> tuple[float, str] | None:
    """
    Find clause end (,;:) closest to the middle of `text` that
    would produce two lines, both within MAX_CHARS_PER_LINE
    """
    CLAUSE_BREAK_COST = 0.0  # breaks at clause end - no cost
    IMBALANCE_PENALTY = 0.5  # each char imbalance adds 0.5 to cost

    mid = len(text) // 2
    if any(c in CLAUSE_ENDS for c in text):
        clause_positions = [idx for idx, c in enumerate(text) if c in CLAUSE_ENDS]
        closest_clause = min(clause_positions, key=lambda i: abs(i - mid))
        if (
            len(text[0 : closest_clause + 1]) > MAX_CHARS_PER_LINE
            or len(text[closest_clause:].lstrip()) > MAX_CHARS_PER_LINE
        ):
            clause_cost = float("inf")
        else:
            clause_cost = CLAUSE_BREAK_COST + (
                abs(mid - closest_clause) * IMBALANCE_PENALTY
            )
        # "Hello, world!" -> "Hello,\nworld!"
        split_text = (
            text[: closest_clause + 1] + "\n" + text[closest_clause + 1 :].lstrip()
        )
        return (clause_cost, split_text)
    else:
        return None


def try_space_break(text: str) -> tuple[float, str] | None:
    """
    Find the space closest to the middle of `text`. Does NOT
    enforce MAX_CHARS_PER_LINE; would rather split once than
    not at all.
    """
    SPACE_BREAK_COST = 5.0  # breaks at a space - has a cost
    IMBALANCE_PENALTY = 0.5  # each char imbalance adds 0.5 to cost

    mid = len(text) // 2

    space_positions = [idx for idx, c in enumerate(text) if c == " "]
    if not space_positions:
        return None
    else:
        closest_space = min(space_positions, key=lambda i: abs(i - mid))
        space_cost = SPACE_BREAK_COST + (abs(mid - closest_space) * IMBALANCE_PENALTY)
        # "Hello, world!" -> "Hello,\nworld!"
        split_text = text[: closest_space + 1] + "\n" + text[closest_space + 1 :]
        return (space_cost, split_text)


def split_cue(cues: list[Cue]) -> list[Cue]:
    """
    Adds line breaks to the cues so they fit on two lines if needed.
    """
    split_cues: list[Cue] = []
    print(f"Attempting to split {len(cues)} cues...")
    for cue in cues:
        # cue is of acceptable length (<= 42) -> pass it on
        if len(cue.text) <= MAX_CHARS_PER_LINE:
            split_cues.append(cue)
            continue

        # Cue is too long -> attempt to split
        candidates: list[tuple[float, str]] = []
        clause = try_clause_break(cue.text)
        if clause:
            candidates.append(clause)
        space = try_space_break(cue.text)
        if space:
            candidates.append(space)

        if not candidates:
            # Must be a word with > 43 chars (very unlikely)
            split_cues.append(cue)
            continue

        cost, broken_text = min(candidates, key=lambda c: c[0])
        split_cues.append(Cue(text=broken_text, start=cue.start, end=cue.end))

    print(f"Successfully split {len(cues)} cues!")
    return split_cues


def cleanup_timings(cues: list[Cue]) -> list[Cue]:
    """
    Clean up timing for each cue,
    add lead in and lead out time while enforcing MIN_GAP
    """
    cleaned_cues: list[Cue] = []
    print(f"Cleaning {len(cues)} cue timings...")
    
    # subtract lead in time from first cue,
    # unless that would be negative in which case leave at 0
    cues[0].start = max(cues[0].start - LEAD_IN_MS, 0)

    # I check cue N + 1, so exclude last cue, it is handled at the bottom
    for i, cue in enumerate(cues[:-1]):
        # if there is enough space between two cues for a lead out and lead in
        # -> apply lead out and lead in (best case)
        if cue.end + LEAD_OUT_MS + MIN_GAP_MS + LEAD_IN_MS <= cues[i + 1].start:
            cue.end += LEAD_OUT_MS
            cues[i + 1].start -= LEAD_IN_MS
        # not enough space for lead out / lead in
        elif cue.end + LEAD_OUT_MS + MIN_GAP_MS + LEAD_IN_MS > cues[i + 1].start:
            # more than `MIN_GAP_MS` between cues ->
            # -> calculate ratio of `LEAD-OUT_MS` to total lead time
                # -> add to cue.end
            # -> calculate ratio of `LEAD_IN_MS` to total lead time
                # -> subtract from cue N + 1 start
            # still respects `MIN_GAP_MS`
            if cues[i + 1].start - cue.end > MIN_GAP_MS:
                time_dif = cues[i + 1].start - cue.end - MIN_GAP_MS
                cue.end += int(time_dif * (LEAD_OUT_MS / (LEAD_OUT_MS + LEAD_IN_MS)))
                cues[i + 1].start -= int(time_dif * (LEAD_IN_MS / (LEAD_OUT_MS + LEAD_IN_MS)))

        cleaned_cues.append(cue)
    last = cues[-1]
    # dont need to touch last.start, the one before already did
    # SRT players allow cue to go past file duration 
    # (which will never happen anyways because movies have credits)
    last.end += LEAD_OUT_MS
    cleaned_cues.append(last)
    print(f"Cleaned {len(cues)} cue timings!")
    return cleaned_cues
