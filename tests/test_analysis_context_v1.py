from copy import deepcopy
import json

from app.analysis_context import readable_context
from app.analysis_stream import messages_for_analysis
from app.models import Profile, Schedule
from app.llm import build_messages


def test_readable_context_translates_metadata_without_rewriting_source():
    source = 'Original source words: True, None, advertised, weekly_double.'
    raw = {'saved_profile': Profile(insurance_requirement='five_and_fund',
                                   accepted_schedules=[Schedule(kind='weekly_double')]).model_dump(),
           'jobs': [{'title': 'base engineer', 'description': source, 'completeness': 'list_only',
                     'evidence': {'raw': source}, 'analysis': {'checks': [
                         {'key': 'schedule', 'label': '休息安排', 'status': 'unknown', 'priority': 'must',
                          'evidence': '岗位 alternating；原文：' + source}], 'questions': [], 'summary': '待确认'}}]}
    before = deepcopy(raw)
    result = readable_context(raw)
    assert raw == before
    assert result['saved_profile']['保险要求'] == '五险一金'
    assert result['saved_profile']['可接受休息安排'][0]['安排'].startswith('每周休两天')
    assert result['jobs'][0]['description'] == source
    assert result['jobs'][0]['title'] == 'base engineer'
    check = result['jobs'][0]['analysis']['逐项核对'][0]
    assert check['结论'] == '待确认'
    assert check['说明与引用'].endswith('；原文：' + source)
    assert 'alternating' not in check['说明与引用'].split('；原文：')[0]


def test_only_analysis_uses_readable_context_not_legacy_structured_editor():
    snapshot = {'profile': Profile(insurance_requirement='five_and_fund'), 'revision': 1, 'messages': []}
    legacy = build_messages(snapshot, '请说明条件', [])
    analysis = messages_for_analysis(snapshot, '请说明条件', [])
    legacy_data = json.loads(legacy[1]['content'].split('\n', 1)[1])
    analysis_data = json.loads(analysis[1]['content'].split('\n', 1)[1])
    assert legacy_data['saved_profile']['insurance_requirement'] == 'five_and_fund'
    assert analysis_data['saved_profile']['保险要求'] == '五险一金'
