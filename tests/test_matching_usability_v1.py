from app.matching import analyze, infer_terms
from app.models import Job, Profile


def test_personal_skills_are_not_an_extra_hard_job_requirement():
    job = Job(id='skills-usability-test', title='客服', company='Synthetic test company', city='郑州',
              description='负责电话回访和来电接听。', completeness='full_jd')
    profile = Profile(city='郑州', target_roles=['客服'], skills=['Excel'], priorities={'role': 'must'})
    report = analyze(job, profile)
    skill = next(c for c in report['checks'] if c['key'] == 'skills')
    assert skill['status'] == 'unknown'
    assert skill['priority'] == 'prefer'
    assert report['verdict'] == 'match'


def test_skills_alone_do_not_make_an_unspecified_profile_a_full_match():
    job = Job(id='skills-only-test', title='数据助理', company='Synthetic test company',
              description='使用 Excel 整理数据。', completeness='full_jd')
    assert analyze(job, Profile(skills=['Excel']))['verdict'] == 'needs_confirmation'


def test_native_list_provenance_survives_repeated_enrichment():
    job = Job(id='native-list-test', title='客服', company='Synthetic test company',
              source='boss', snippet_only=True, completeness='list_only')
    for _ in range(3):
        job = infer_terms(job)
    assert job.completeness == 'list_only'
    assert analyze(job, Profile(target_roles=['客服']))['verdict'] == 'needs_confirmation'


def test_legacy_search_excerpt_still_cannot_become_a_full_jd():
    job = Job(id='legacy-excerpt-test', title='客服', company='Synthetic test company',
              description='Synthetic excerpt only.', snippet_only=True, completeness='user_provided')
    assert infer_terms(job).completeness == 'search_snippet'
