import numpy as np

from praattypen.batch import Turn, format_script, merge_turns, speaker_labels, split_long_turns


def test_merge_turns_joins_same_speaker_and_folds_scraps() -> None:
    turns = merge_turns(
        [
            Turn(0.0, 3.0, "S1"),
            Turn(3.4, 5.0, "S1"),  # same speaker after a short pause
            Turn(5.1, 5.3, "S2"),  # a scrap
            Turn(5.5, 9.0, "S2"),
        ]
    )
    assert [(t.speaker, t.start, t.end) for t in turns] == [("S1", 0.0, 5.3), ("S2", 5.5, 9.0)]


def test_long_turns_are_cut_into_chunks_of_at_most_a_minute() -> None:
    audio = np.random.default_rng(0).normal(0, 0.1, 16000 * 150).astype(np.float32)
    audio[16000 * 50 : 16000 * 51] = 0.0  # a pause to cut at
    chunks = split_long_turns(audio, [Turn(0.0, 150.0, "S1")])
    assert all(c.end - c.start <= 60.0 for c in chunks)
    assert 49.5 <= chunks[0].end <= 51.5
    assert chunks[-1].end == 150.0


def test_script_uses_letters_in_order_of_appearance_and_dutch_labels() -> None:
    turns = [Turn(0, 4, "SPEAKER_01", "Goedemiddag."), Turn(4, 9, "SPEAKER_00", "Hallo."),
             Turn(9, 12, "SPEAKER_00", "Nog iets.")]
    labels = speaker_labels(turns)
    text = format_script(turns, labels, "Test", 12, "nl", "whisper-large-v3-mlx")
    assert "[00:00] Spreker A: Goedemiddag." in text
    assert "[00:04] Spreker B: Hallo. Nog iets." in text
    assert "2 sprekers" in text
