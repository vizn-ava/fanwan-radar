import pytest

from app.llm import ModelError
from app.models import Profile, Schedule
from app.response_guard import ScheduleClaimGuard, contradicts_weekly_rest


@pytest.mark.parametrize('text', [
    '你要求每周休两天，大小周通常满足此要求，但需确认具体频率。',
    '单双休符合你的双休要求。',
    '大小周可以保证每周休息2天。',
    '大小周等于每周双休。',
])
def test_rejects_positive_false_claims(text):
    assert contradicts_weekly_rest(text, True)


@pytest.mark.parametrize('text', [
    '大小周包含单休周，不满足每周休两天。',
    '大小周不能保证每周休两天。',
    '大小周未必能满足此要求。',
    '大小周满足不了每周休两天。',
    '大小周是否满足你的要求，必须先确认实际安排。',
    '如果你改为接受大小周，可以满足调整后的要求，但不是每周双休。',
    '大小周满足每周休两天吗？',
    '大小周符合公司的安排，是否接受需要你判断。',
    '如果你愿意接受大小周，这种安排就符合你的要求。',
])
def test_preserves_negation_questions_and_changed_requirements(text):
    assert not contradicts_weekly_rest(text, True)


@pytest.mark.parametrize('width', [1, 2, 3, 7, 100])
def test_false_sentence_never_escapes_across_chunk_boundaries(width):
    guard = ScheduleClaimGuard(Profile(accepted_schedules=[Schedule(kind='weekly_double')]))
    source = '先看工资信息。大小周通常满足此要求，但需确认执行频率。'
    emitted = []
    with pytest.raises(ModelError, match='大小周'):
        for i in range(0, len(source), width):
            emitted.extend(guard.feed(source[i:i+width]))
        emitted.append(guard.finish())
    assert '通常满足' not in ''.join(emitted)
    assert '大小周' not in ''.join(emitted)


def test_short_normal_tokens_are_not_held_until_full_completion():
    guard = ScheduleClaimGuard(Profile())
    assert guard.feed('你') == ['你']
    assert guard.feed('好') == ['好']
    assert guard.finish() == ''


def test_single_rest_requirement_does_not_reject_valid_alternating_fit():
    text = '大小周满足你的要求。'
    assert not contradicts_weekly_rest(text, False)


def test_alternative_accepted_schedules_do_not_become_an_all_weekly_requirement():
    profile = Profile(accepted_schedules=[Schedule(kind='weekly_double'), Schedule(kind='alternating')])
    guard = ScheduleClaimGuard(profile)
    assert guard.requires_weekly is False
    assert ''.join(guard.feed('大小周满足你的要求。')) + guard.finish() == '大小周满足你的要求。'
    # Even when the user accepts it, do not redefine alternating weeks as two
    # days off in every week.
    assert contradicts_weekly_rest('大小周能够保证每周休两天。', guard.requires_weekly)
