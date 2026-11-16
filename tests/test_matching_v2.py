"""Synthetic matching fixtures only; no app, storage, credentials or live sources."""
import unittest

from app.matching import analyze, detect_skills, infer_terms, salary_range
from app.models import Job, Profile, Schedule


def job(**values):
    return Job(**({'title': 'Python开发', 'description': '负责软件开发与维护。',
                   'completeness': 'full_jd'} | values))


def check(result, key):
    return next(c for c in result['checks'] if c['key'] == key)


class MatchingV2Tests(unittest.TestCase):
    def test_salary_units_and_extra_pay(self):
        for text, expected in [('6K-8K', (6000, 8000)), ('6-8K', (6000, 8000)),
                               ('6千至8千', (6000, 8000)), ('0.6万-0.8万', (6000, 8000)),
                               ('6000-8000元/月·13薪', (6000, 8000)),
                               ('6k-8k·13薪', (6000, 8000)), ('月薪6000元', (6000, 6000))]:
            with self.subTest(text=text):
                self.assertEqual(salary_range(text), expected)

    def test_native_salary_periods(self):
        for text, period, lo in [('年薪20-30万', 'year', 200000), ('20K-30K/年', 'year', 20000),
                                 ('日薪200-300元', 'day', 200), ('200元/天', 'day', 200),
                                 ('时薪25-30元', 'hour', 25), ('30元/小时', 'hour', 30)]:
            with self.subTest(text=text):
                self.assertEqual(salary_range(text), (None, None))
                j = infer_terms(job(salary_text=text))
                self.assertEqual((j.salary_min, j.salary_period), (lo, period))
                self.assertEqual(check(analyze(j, Profile(salary_min=5000)), 'salary')['status'], 'unknown')
                self.assertEqual(check(analyze(j, Profile(salary_min=lo, salary_period=period)), 'salary')['status'], 'pass')

    def test_base_and_advertised_are_distinct(self):
        j = job(salary_text='8K-12K', salary_min=8000, salary_max=12000,
                salary_period='month', salary_basis='advertised', description='无责底薪4000元/月，综合收入8K-12K。')
        inferred = infer_terms(j)
        self.assertEqual((inferred.salary_min, inferred.salary_basis), (8000, 'advertised'))
        self.assertEqual(check(analyze(j, Profile(salary_min=6000, salary_basis='base')), 'salary')['status'], 'fail')
        self.assertEqual(check(analyze(j, Profile(salary_min=6000, salary_basis='advertised')), 'salary')['status'], 'pass')
        self.assertEqual(check(analyze(job(salary_text='6K-8K'), Profile(salary_min=7000)), 'salary')['status'], 'unknown')
        self.assertEqual(check(analyze(job(salary_text='6K-8K'), Profile(salary_max=5000)), 'salary')['status'], 'fail')

    def test_ambiguous_base_does_not_take_later_total(self):
        j = job(salary_text='8K-12K', description='底薪面议，综合收入8000元/月。')
        self.assertEqual(check(analyze(j, Profile(salary_min=6000, salary_basis='base')), 'salary')['status'], 'unknown')
        self.assertEqual(salary_range('6000'), (6000, 6000))

    def test_known_structured_values_not_overwritten(self):
        j = job(housing_fund=False, social_insurance=False, schedule_kind='weekly_single',
                work_hours_daily=12, salary_min=9000, salary_max=10000, salary_basis='advertised',
                salary_period='year', description='五险一金，周末双休，8小时制，底薪6000元。')
        k = infer_terms(j)
        self.assertEqual((k.housing_fund, k.social_insurance, k.schedule_kind, k.work_hours_daily),
                         (False, False, 'weekly_single', 12))
        self.assertEqual((k.salary_min, k.salary_max, k.salary_period), (9000, 10000, 'year'))
        self.assertEqual(j.evidence, {})

    def test_insurance_and_fund_separate(self):
        j = job(description='缴纳五险。')
        self.assertTrue(infer_terms(j).social_insurance)
        self.assertIsNone(infer_terms(j).housing_fund)
        self.assertEqual(check(analyze(j, Profile(insurance_requirement='five_insurances')), 'insurance')['status'], 'pass')
        self.assertEqual(check(analyze(j, Profile(insurance_requirement='five_and_fund')), 'insurance')['status'], 'unknown')

    def test_negations_and_conflicting_welfare(self):
        for text in ['不缴纳五险一金', '不缴五险一金', '没有五险一金', '五险一金需自理', '不提供公积金', '不保证五险一金']:
            with self.subTest(text=text):
                j = infer_terms(job(description=text))
                self.assertFalse(j.housing_fund)
                self.assertNotEqual(check(analyze(j, Profile(insurance_requirement='five_and_fund')), 'insurance')['status'], 'pass')
        j = job(description='五险一金。实际不交公积金。')
        result = check(analyze(j, Profile(insurance_requirement='five_and_fund')), 'insurance')
        self.assertEqual(result['status'], 'unknown')
        self.assertIn('五险一金', result['evidence'])
        self.assertIn('不交公积金', result['evidence'])

    def test_structured_text_disagreement_stays_unknown_without_overwrite(self):
        j = job(housing_fund=True, social_insurance=True, description='五险，无公积金。')
        self.assertTrue(infer_terms(j).housing_fund)
        self.assertEqual(check(analyze(j, Profile(insurance_requirement='five_and_fund')), 'insurance')['status'], 'unknown')

    def test_probation_and_fund_base_not_invented(self):
        p = Profile(insurance_during_probation=True, fund_base_min=5000)
        r = analyze(job(description='五险一金。'), p)
        self.assertEqual(check(r, 'insurance_probation')['status'], 'unknown')
        self.assertEqual(check(r, 'fund_base')['status'], 'unknown')
        r = analyze(job(description='试用期不交社保，公积金基数4000元。'), p)
        self.assertEqual(check(r, 'insurance_probation')['status'], 'fail')
        self.assertEqual(check(r, 'fund_base')['status'], 'fail')

    def test_weekend_is_stricter_than_two_rest_days(self):
        p = Profile(accepted_schedules=[Schedule(kind='weekend_double')])
        self.assertEqual(check(analyze(job(description='每周休两天。'), p), 'schedule')['status'], 'unknown')
        self.assertEqual(check(analyze(job(description='每周一、周二休息。'), p), 'schedule')['status'], 'fail')
        self.assertEqual(check(analyze(job(description='周末双休。'), p), 'schedule')['status'], 'pass')
        p.accepted_schedules = [Schedule(kind='weekly_double')]
        self.assertEqual(check(analyze(job(description='周末双休。'), p), 'schedule')['status'], 'pass')

    def test_cycles_are_not_shift_or_hours_guarantees(self):
        for text, work, rest in [('上三休一', 3, 1), ('上二休二', 2, 2), ('做12天休2天', 12, 2)]:
            with self.subTest(text=text):
                p = Profile(accepted_schedules=[Schedule(kind='custom_cycle', work_days=work, rest_days=rest)],
                            accept_night_shift=False, max_daily_hours=8)
                r = analyze(job(description=text + '，12小时制，夜班。'), p)
                self.assertEqual(check(r, 'schedule')['status'], 'pass')
                self.assertEqual(check(r, 'hours')['status'], 'fail')
                self.assertEqual(check(r, 'night_shift')['status'], 'fail')
                self.assertEqual(r['verdict'], 'mismatch')
                r = analyze(job(description=text), p)
                self.assertEqual(check(r, 'hours')['status'], 'unknown')
                self.assertEqual(check(r, 'night_shift')['status'], 'unknown')

    def test_alternating_rotation_and_contradictory_schedules(self):
        for text, kind in [('大小周', 'alternating'), ('单双休', 'alternating'), ('轮休', 'rotation'), ('单休', 'weekly_single'), ('双休', 'weekly_double')]:
            with self.subTest(text=text):
                self.assertEqual(infer_terms(job(description=text)).schedule_kind, kind)
        self.assertEqual(check(analyze(job(description='周末双休；实际单休。'),
                                     Profile(accepted_schedules=[Schedule(kind='weekend_double')])), 'schedule')['status'], 'unknown')

    def test_shift_employment_and_work_time(self):
        j = job(description='全职，非外包，非劳务派遣，无夜班，无需倒班，不加班，08:30-17:30，8小时制。')
        p = Profile(employment_types=['全职'], accept_outsourcing=False, accept_dispatch=False,
                    accept_night_shift=False, accept_rotating_shift=False, accept_overtime=False,
                    work_start='08:00', work_end='18:00', max_daily_hours=8)
        r = analyze(j, p)
        self.assertEqual(r['verdict'], 'match')
        self.assertTrue(all(c['status'] == 'pass' for c in r['checks']))

    def test_priorities_change_verdict_not_facts(self):
        j = job(salary_text='3K-4K')
        must = analyze(j, Profile(salary_min=6000))
        soft = analyze(j, Profile(salary_min=6000, target_roles=['Python'], priorities={'salary': 'prefer'}))
        ignored = analyze(j, Profile(salary_min=6000, target_roles=['Python'], priorities={'salary': 'ignore'}))
        self.assertEqual(must['verdict'], 'mismatch')
        self.assertEqual(soft['verdict'], 'match')
        self.assertEqual(check(soft, 'salary')['priority'], 'prefer')
        self.assertLess(soft['score'], ignored['score'])
        self.assertFalse(any(c['key'] == 'salary' for c in ignored['checks']))
        soft_unknown = analyze(job(), Profile(salary_min=6000, target_roles=['Python'], priorities={'salary': 'prefer'}))
        self.assertEqual(check(soft_unknown, 'salary')['status'], 'unknown')
        self.assertEqual(soft_unknown['verdict'], 'match')

    def test_pending_profile_condition_cannot_pass(self):
        r = analyze(job(salary_text='8K-10K'), Profile(salary_min=6000, confirmation_required=['salary_min']))
        self.assertEqual(check(r, 'salary')['status'], 'unknown')
        self.assertEqual(r['verdict'], 'needs_confirmation')

    def test_clearing_conditions_and_historic_notes(self):
        p = Profile(salary_min=0, insurance_requirement='any', accepted_schedules=[],
                    require_double_rest=True, require_housing_fund=True,
                    notes='以前不做销售，不接受外包、夜班，通勤30分钟', excluded_terms=[])
        r = analyze(job(description='销售外包夜班'), p)
        self.assertEqual([c['key'] for c in r['checks']], ['profile'])
        self.assertEqual(r['verdict'], 'needs_confirmation')

    def test_current_exclusions_and_contradictions(self):
        p = Profile(excluded_terms=['销售'])
        for text, expected in [('涉及销售', 'fail'), ('不涉及销售', 'pass'), ('负责运营', 'unknown'),
                               ('不涉及销售。主要负责销售。', 'unknown')]:
            with self.subTest(text=text):
                self.assertEqual(check(analyze(job(description=text), p), 'exclusion_销售')['status'], expected)

    def test_legacy_explicit_conditions(self):
        p = Profile(require_double_rest=True, require_housing_fund=True)
        self.assertEqual(analyze(job(description='周末双休，五险一金。'), p)['verdict'], 'match')
        p.confirmation_required = ['require_double_rest', 'require_housing_fund']
        self.assertEqual(analyze(job(description='周末双休，五险一金。'), p)['verdict'], 'needs_confirmation')

    def test_education_and_experience_thresholds_and_missing_jd(self):
        p = Profile(education='本科', experience_years=3)
        r = analyze(job(description='要求大专以上，2-4年工作经验。'), p)
        self.assertEqual(check(r, 'education')['status'], 'pass')
        self.assertEqual(check(r, 'experience')['status'], 'pass')
        r = analyze(job(description='要求硕士以上，5年以上工作经验。'), p)
        self.assertEqual(check(r, 'education')['status'], 'fail')
        self.assertEqual(check(r, 'experience')['status'], 'fail')
        r = analyze(job(), p)
        self.assertEqual(check(r, 'education')['status'], 'unknown')
        self.assertEqual(check(r, 'experience')['status'], 'unknown')
        self.assertEqual(analyze(job(description='学历不限，经验不限。'), p)['verdict'], 'match')

    def test_multiple_cities_districts_and_remote_restrictions(self):
        p = Profile(cities=['杭州', '上海'], districts=['滨江区', '浦东新区'])
        self.assertEqual(check(analyze(job(city='上海市浦东新区'), p), 'location')['status'], 'pass')
        self.assertEqual(check(analyze(job(city='上海'), p), 'location')['status'], 'unknown')
        self.assertEqual(check(analyze(job(city='上海市徐汇区'), p), 'location')['status'], 'fail')
        self.assertEqual(check(analyze(job(city='北京'), p), 'location')['status'], 'fail')
        p.allow_remote = True
        r = analyze(job(city='远程'), p)
        self.assertEqual(check(r, 'location')['status'], 'unknown')
        self.assertEqual(check(r, 'remote')['status'], 'unknown')

    def test_commute_without_map_always_unknown(self):
        r = analyze(job(description='通勤仅10分钟。'), Profile(max_commute_minutes=30))
        self.assertEqual(check(r, 'commute')['status'], 'unknown')

    def test_completeness_and_empty_profile(self):
        self.assertEqual(analyze(job(), Profile())['verdict'], 'needs_confirmation')
        for completeness in ['list_only', 'search_snippet']:
            with self.subTest(completeness=completeness):
                r = analyze(job(completeness=completeness), Profile(target_roles=['Python']))
                self.assertEqual(r['verdict'], 'needs_confirmation')
                self.assertIn(completeness, check(r, 'completeness')['evidence'])
        self.assertEqual(analyze(job(description=''), Profile(target_roles=['Python']))['verdict'], 'needs_confirmation')
        self.assertEqual(infer_terms(job(snippet_only=True)).completeness, 'search_snippet')
        self.assertEqual(infer_terms(job(completeness='user_provided')).completeness, 'user_provided')

    def test_interfaces_no_invented_contacts_credentials_or_skills(self):
        j = job(description='Python开发，联系详情请到原平台。')
        k = infer_terms(j)
        self.assertEqual(k.contacts, [])
        self.assertEqual(k.posted_at, '')
        self.assertEqual(k.source_url, '')
        self.assertEqual(detect_skills('JavaScript Python'), ['Python', 'JavaScript'])
        r = analyze(j, Profile(skills=['Python']))
        self.assertEqual(set(r), {'score', 'verdict', 'checks', 'strengths', 'gaps', 'questions', 'summary'})
        self.assertTrue(all('priority' in c for c in r['checks']))


if __name__ == '__main__':
    unittest.main()
