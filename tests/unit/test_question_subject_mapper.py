from types import SimpleNamespace

from app.services.question_subject_mapper import _extract_official_subjects, _match_subject_label


def _subject(name: str):
    return SimpleNamespace(display_name=name)


def test_extract_official_subjects_from_calbar_cover_table() -> None:
    instructions = """
Question Number               Subject

       1.                          Wills

       2.                    Remedies / Torts

       3.                      Evidence

       4.                      Business Associations

       5.                        Professional Responsibility

       6.                        Criminal Law and Procedure
"""

    assert _extract_official_subjects(instructions) == {
        1: "Wills",
        2: "Remedies / Torts",
        3: "Evidence",
        4: "Business Associations",
        5: "Professional Responsibility",
        6: "Criminal Law and Procedure",
    }


def test_match_subject_label_uses_exact_official_subject() -> None:
    subjects = [_subject("Wills"), _subject("Evidence"), _subject("Community Property")]

    matched = _match_subject_label(
        "Wills",
        "Mary wrote a will and later signed an undated holographic will.",
        subjects,
    )

    assert matched.display_name == "Wills"


def test_match_subject_label_uses_keywords_for_mixed_subjects() -> None:
    subjects = [_subject("Legal Remedies"), _subject("Torts")]

    matched = _match_subject_label(
        "Remedies / Torts",
        "Plaintiff seeks an injunction, specific performance, and restitution.",
        subjects,
    )

    assert matched.display_name == "Legal Remedies"
