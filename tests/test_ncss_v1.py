"""Synthetic adapter fixtures; live evidence is recorded separately."""
from app.models import Profile
from app.matching import analyze
from app.query_planner import build_search_plan
from app.source_ncss import normalize, parse_detail, region_code


def row(**updates):
    return {'jobId': 'test_public_job_123', 'jobName': '客服专员', 'recName': '测试公司',
            'lowMonthPay': 6.0, 'highMonthPay': 8.0, 'areaCodeName': '河南',
            'degreeName': '本科及以上', 'sourcesNameCh': '测试高校', **updates}


def test_region_codes_use_bundled_official_catalog():
    assert region_code('郑州') == '410100'
    assert region_code('北京市') == '11'
    try:
        region_code('不存在的城市')
    except ValueError:
        pass
    else:
        raise AssertionError('Unrecognized city must not fall back to nationwide')


def test_public_raw_list_is_not_fabricated_full_jd():
    job = normalize(row())
    assert job.source == 'ncss'
    assert job.salary_min == 6000 and job.salary_max == 8000
    assert job.salary_period == 'month'
    assert job.completeness == 'list_only'
    assert job.city == ''  # A province is not proof of a requested city.
    assert job.source_url.endswith('/test_public_job_123/detail.html')
    short = normalize(row(), {'description': '客服专员', 'location': '河南省郑州市'})
    assert short.completeness == 'list_only'
    assert short.city == '河南省郑州市'


def test_detail_parser_ignores_scripts_and_footer_claims():
    html = '<html><head><title>客服岗位</title><script>虚构薪资90000元</script></head><body><div class="site-tag">河南省郑州市</div><span id="realCorpName">测试公司</span><div class="mainContent mainContent-geshi"><p>负责客户咨询、售后工单和订单回访。</p><p>固定底薪6000元，周末双休，五险一金。</p></div><footer>非职位内容：培训推广</footer></body></html>'
    detail = parse_detail(html)
    assert detail['location'] == '河南省郑州市'
    assert '90000' not in detail['description']
    assert '推广' not in detail['description']
    job = normalize(row(), detail)
    assert job.completeness == 'full_jd'
    assert job.salary_basis == 'advertised'  # Preserve the API headline fact.
    assert job.salary_min == 6000
    checks = analyze(job, Profile(salary_min=6000, salary_basis='base'))['checks']
    assert next(c for c in checks if c['key'] == 'salary')['status'] == 'pass'


def test_ncss_plan_has_actual_location_and_keyword_filters():
    plan = build_search_plan(Profile(cities=['郑州'], target_roles=['客服']), sources=['ncss'])
    query = plan['sources'][0]['queries'][0]
    assert query['platform_filters'] == {'areaCode': '410100', 'jobName': '客服'}
    assert query['max_list'] == 10
    assert plan['sources'][0]['enabled']
