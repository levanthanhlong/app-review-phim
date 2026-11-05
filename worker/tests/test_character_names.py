"""People's names said in the dialogue, found by the LLM and checked against the dialogue."""

from __future__ import annotations

from recap.character_names import NameCandidate, find_names
from recap.llm import LLMError
from recap.models import TranscriptSegment


def line(start: float, text: str) -> TranscriptSegment:
    return TranscriptSegment(id=int(start), start=start, end=start + 2, text=text)


DIALOGUE = [
    line(10, "Hiro... ไขยวะ"),
    line(20, "Pan ơi, đợi anh với!"),
    line(30, "Anh Hiro đến từ Osaka à?"),
    line(40, "hiro, cảm ơn nhé"),
    line(50, "Chúng ta đi Tokyo thôi."),
]


class FakeLLM:
    def __init__(self, *answers: str | Exception) -> None:
        self.answers = list(answers)
        self.inputs: list[str] = []

    def generate(self, prompt: str, input_text: str) -> str:
        self.inputs.append(input_text)
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer


ANSWER = """```json
[{"name": "Hiro", "original": "Hiro"}, {"name": "Pan", "original": "Pan"},
 {"name": "Osaka", "original": "Osaka"}, {"name": "Mai", "original": "Mai"}]
```"""


def test_names_are_counted_with_real_lines_and_invented_ones_dropped() -> None:
    llm = FakeLLM(ANSWER)

    names, warning = find_names(DIALOGUE, llm)

    assert warning is None
    assert "[00:00:10] Hiro... ไขยวะ" in llm.inputs[0]
    # Mai is said nowhere: dropped; Osaka is kept (only the user can tell a place from a person here)
    assert [n.name for n in names] == ["Hiro", "Pan", "Osaka"]
    hiro = names[0]
    assert hiro == NameCandidate(name="Hiro", count=3, examples=("Hiro... ไขยวะ", "Anh Hiro đến từ Osaka à?", "hiro, cảm ơn nhé"))


def test_a_name_in_another_script_keeps_its_vietnamese_spelling() -> None:
    llm = FakeLLM('[{"name": "Pim", "original": "ไขยวะ"}]')

    names, _ = find_names(DIALOGUE, llm)

    assert names == [NameCandidate(name="Pim", count=1, examples=("Hiro... ไขยวะ",))]


def test_a_bad_answer_is_asked_again_then_given_up_with_a_warning() -> None:
    names, warning = find_names(DIALOGUE, FakeLLM("không biết", "vẫn không phải JSON"))

    assert names == [] and warning is not None


def test_no_dialogue_needs_no_call() -> None:
    llm = FakeLLM()

    assert find_names([], llm) == ([], None)


def test_llm_errors_become_a_warning_not_a_failure() -> None:
    names, warning = find_names(DIALOGUE, FakeLLM(LLMError("llm_quota", "Hết hạn mức")))

    assert names == [] and "Hết hạn mức" in (warning or "")
