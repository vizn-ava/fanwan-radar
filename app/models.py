from __future__ import annotations

from typing import Literal
from uuid import uuid4
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

ScheduleKind = Literal['weekend_double', 'weekly_double', 'weekly_single', 'alternating', 'rotation', 'custom_cycle']
PriorityKey = Literal['location', 'salary', 'insurance', 'schedule', 'hours', 'night_shift',
                      'rotating_shift', 'overtime', 'work_time', 'employment', 'industry',
                      'excluded_terms', 'outsourcing', 'dispatch', 'remote', 'commute',
                      'role', 'education', 'experience']
Priority = Literal['must', 'prefer', 'ignore']
RealSource = Literal['boss', 'zhaopin', 'liepin', 'ncss', 'public', 'import']


class Schedule(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    kind: ScheduleKind
    work_days: int | None = Field(default=None, ge=1, le=366)
    rest_days: int | None = Field(default=None, ge=0, le=366)
    fixed_weekdays: list[int] = Field(default_factory=list, max_length=7)

    @field_validator('fixed_weekdays')
    @classmethod
    def weekdays(cls, value):
        if any(day < 1 or day > 7 for day in value):
            raise ValueError('Weekdays must be between 1 and 7')
        return list(dict.fromkeys(value))


class Profile(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    name: str = Field(default='', max_length=80)
    city: str = Field(default='', max_length=100)
    target_roles: list[str] = Field(default_factory=list, max_length=20)
    skills: list[str] = Field(default_factory=list, max_length=60)
    experience_years: float | None = Field(default=None, ge=0, le=70)
    education: str = Field(default='', max_length=40)
    salary_min: int = Field(default=0, ge=0, le=1000000)
    salary_max: int | None = Field(default=None, ge=0, le=1000000)
    salary_period: Literal['month', 'year', 'day', 'hour'] = 'month'
    salary_basis: Literal['unspecified', 'base', 'advertised'] = 'unspecified'
    cities: list[str] = Field(default_factory=list, max_length=50)
    districts: list[str] = Field(default_factory=list, max_length=100)
    insurance_requirement: Literal['any', 'five_insurances', 'five_and_fund'] = 'any'
    insurance_during_probation: bool | None = None
    fund_base_min: int | None = Field(default=None, ge=0)
    accepted_schedules: list[Schedule] = Field(default_factory=list, max_length=20)
    max_daily_hours: float | None = Field(default=None, gt=0, le=24)
    accept_night_shift: bool | None = None
    accept_rotating_shift: bool | None = None
    accept_overtime: bool | None = None
    work_start: str = Field(default='', pattern=r'^(?:[01][0-9]|2[0-3]):[0-5][0-9]$|^$')
    work_end: str = Field(default='', pattern=r'^(?:[01][0-9]|2[0-3]):[0-5][0-9]$|^$')
    employment_types: list[str] = Field(default_factory=list, max_length=30)
    industries: list[str] = Field(default_factory=list, max_length=50)
    excluded_terms: list[str] = Field(default_factory=list, max_length=100)
    accept_outsourcing: bool | None = None
    accept_dispatch: bool | None = None
    allow_remote: bool | None = None
    allow_relocation: bool | None = None
    max_commute_minutes: int | None = Field(default=None, ge=0, le=1440)
    priorities: dict[PriorityKey, Priority] = Field(default_factory=dict)
    field_sources: dict[str, str] = Field(default_factory=dict)
    confirmation_required: list[str] = Field(default_factory=list)
    require_double_rest: bool | None = None
    require_housing_fund: bool | None = None
    resume_text: str = Field(default='', max_length=60000)
    notes: str = Field(default='', max_length=8000)

    @model_validator(mode='after')
    def salary_bounds(self):
        if self.salary_max is not None and self.salary_max < self.salary_min:
            raise ValueError('salary_max must not be less than salary_min')
        return self

    @field_validator('target_roles', 'skills', 'cities', 'districts', 'employment_types', 'industries', 'excluded_terms')
    @classmethod
    def clean_list(cls, value: list[str]) -> list[str]:
        return list(dict.fromkeys(v.strip()[:80] for v in value if v.strip()))


class Contact(BaseModel):
    kind: Literal['email', 'phone', 'url']
    value: str = Field(max_length=400)
    label: str = Field(default='招聘原文公开联系方式', max_length=100)


class Job(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False)
    id: str = ''
    title: str = Field(max_length=200)
    company: str = Field(default='未注明', max_length=200)
    city: str = Field(default='', max_length=150)
    salary_text: str = Field(default='未注明', max_length=150)
    salary_min: int | None = Field(default=None, ge=0)
    salary_max: int | None = Field(default=None, ge=0)
    salary_basis: Literal['base', 'advertised', 'unknown'] = 'unknown'
    description: str = Field(default='', max_length=60000)
    skills: list[str] = Field(default_factory=list, max_length=100)
    education: str = Field(default='', max_length=40)
    experience_min: float | None = Field(default=None, ge=0, le=70)
    double_rest: bool | None = None
    housing_fund: bool | None = None
    source: Literal['boss', 'zhaopin', 'liepin', 'ncss', 'public', 'import', 'demo'] = 'import'
    source_url: str = Field(default='', max_length=2000)
    collected_at: str = ''
    is_demo: bool = False
    contacts: list[Contact] = Field(default_factory=list, max_length=20)
    snippet_only: bool = False
    social_insurance: bool | None = None
    insurance_during_probation: bool | None = None
    fund_base: int | None = Field(default=None, ge=0)
    salary_period: Literal['month', 'year', 'day', 'hour', 'unknown'] = 'unknown'
    salary_source: str = ''
    schedule_kind: Literal['weekend_double', 'weekly_double', 'weekly_single', 'alternating', 'rotation', 'custom_cycle', 'unknown'] = 'unknown'
    cycle_work_days: int | None = Field(default=None, ge=1, le=366)
    cycle_rest_days: int | None = Field(default=None, ge=0, le=366)
    weekly_rest_days: int | None = Field(default=None, ge=0, le=7)
    fixed_rest_weekdays: list[int] = Field(default_factory=list, max_length=7)
    work_hours_daily: float | None = Field(default=None, gt=0, le=24)
    night_shift: bool | None = None
    rotating_shift: bool | None = None
    overtime: bool | None = None
    work_start: str = ''
    work_end: str = ''
    employment_type: str = ''
    industry: str = ''
    outsourced: bool | None = None
    dispatched: bool | None = None
    evidence: dict[str, str] = Field(default_factory=dict)
    completeness: Literal['full_jd', 'list_only', 'search_snippet', 'user_provided'] = 'user_provided'
    posted_at: str = ''

    @model_validator(mode='after')
    def salary_bounds(self):
        if self.salary_min is not None and self.salary_max is not None and self.salary_max < self.salary_min:
            raise ValueError('salary_max must not be less than salary_min')
        return self

    @field_validator('fixed_rest_weekdays')
    @classmethod
    def weekdays(cls, value):
        return Schedule.weekdays(value)

    @field_validator('source_url')
    @classmethod
    def validate_source(cls, value: str) -> str:
        from urllib.parse import urlsplit
        if value and (urlsplit(value).scheme not in ('http', 'https') or not urlsplit(value).hostname):
            raise ValueError('岗位来源只能使用 http/https 链接')
        if value and (urlsplit(value).username is not None or urlsplit(value).password is not None):
            raise ValueError('Source URLs must not contain credentials')
        return value


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    message: str = Field(min_length=1, max_length=8000)
    request_id: str = Field(default_factory=lambda: str(uuid4()), min_length=1, max_length=200)
    expected_revision: int | None = Field(default=None, ge=0)
    generation: int | None = Field(default=None, ge=0)
    job_ids: list[str] = Field(default_factory=list, max_length=5)

    @field_validator('job_ids')
    @classmethod
    def selected_jobs(cls, value):
        if any(not v.strip() or len(v) > 200 for v in value):
            raise ValueError('Invalid selected job ID')
        return list(dict.fromkeys(value))


class ProfileUpdate(BaseModel):
    model_config = ConfigDict(extra='forbid')
    patch: dict = Field(default_factory=dict)
    expected_revision: int | None = Field(default=None, ge=0)
    generation: int | None = Field(default=None, ge=0)

    @field_validator('patch')
    @classmethod
    def allowed_patch(cls, value):
        if set(value) - (set(Profile.model_fields) - {'field_sources', 'confirmation_required'}):
            raise ValueError('Unknown or protected profile patch fields')
        return value


class SearchRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    sources: list[RealSource] = Field(default_factory=lambda: ['boss'], min_length=1, max_length=5)
    source: RealSource | None = None
    keyword: str = Field(default='', max_length=150)
    expected_revision: int | None = Field(default=None, ge=0)
    generation: int | None = Field(default=None, ge=0)

    @model_validator(mode='after')
    def legacy_source(self):
        if self.source is not None:
            if 'sources' in self.model_fields_set and self.sources != [self.source]:
                raise ValueError('source and sources disagree')
            self.sources = [self.source]
        self.sources = list(dict.fromkeys(self.sources))
        return self


class ImportRequest(BaseModel):
    jobs: list[Job] = Field(min_length=1, max_length=200)
