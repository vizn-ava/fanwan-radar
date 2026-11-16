"""Reject common, explicit schedule contradictions before streaming the claim.

This is a narrow consistency check, not a general factual-verification engine.
It never substitutes rule-generated prose for a purported model response.
"""
import re

from .llm import ModelError

ALTERNATING = ('大小周', '单双休', '隔周双休', '隔周休两天')
PREDICATES = ('满足', '符合', '达到', '保证', '等于', '等同于', '相当于')
WEEKLY = re.compile(r'(?:每周|周周)(?:固定|至少|均|都|稳定)?(?:休(?:息)?[两二2]天|双休|有[两二2]天休息)|每个(?:自然)?周.{0,4}休[两二2]天')
POSITIVE = re.compile('|'.join(PREDICATES))
END = re.compile(r'[。！？!?]|\n\s*\n')


def contradicts_weekly_rest(text: str, requires_weekly: bool) -> bool:
    if not any(word in text for word in ALTERNATING):
        return False
    for match in POSITIVE.finditer(text):
        before = re.split(r'[，,。；;！？!?\n]', text[:match.start()])[-1]
        # Questions, uncertainty and negation are not positive claims.
        if re.search(r'(?:不|未必|无法|不能|难以|并非|是否|能否|可否|不知)[^，。；;\n]{0,10}$', before):
            continue
        after = re.split(r'[，,。；;！？!?\n]', text[match.end():], maxsplit=1)[0][:60]
        if re.match(r'\s*(?:不了|不到|不成)', after) or re.search(r'[吗么][”"\']?$', after):
            continue
        explicit = bool(WEEKLY.search(after))
        reference = requires_weekly and bool(re.search(r'此(?:项)?要求|这(?:项|个)?要求|你(?:的)?(?:休息|双休|排班)?要求|您(?:的)?(?:休息|双休|排班)?要求', after))
        if not explicit and not reference:
            continue
        # Discussing genuinely relaxed/new requirements is allowed, but cannot
        # redefine the explicit requirement of two days off in every week.
        if not explicit and (re.search(r'调整后|放宽后|新的要求|新条件|改为接受', text)
                             or re.search(r'(?:如果|若|既然|现在).{0,12}接受(?:大小周|单双休)', text)):
            continue
        near = text[max(0, match.start()-120):match.start()]
        if any(word in near for word in ALTERNATING):
            return True
    return False


class ScheduleClaimGuard:
    def __init__(self, profile):
        data = profile.model_dump() if hasattr(profile, 'model_dump') else profile
        pending = set(data.get('confirmation_required', []))
        schedules = data.get('accepted_schedules', [])
        self.requires_weekly = (data.get('priorities', {}).get('schedule') != 'ignore'
                                and not pending.intersection({'accepted_schedules', 'require_double_rest'})
                                and bool(schedules)
                                and all(s.get('kind') in ('weekly_double', 'weekend_double') for s in schedules))
        self.pending, self.history, self.active = '', '', False

    def _validate(self, sentence):
        if contradicts_weekly_rest(self.history[-120:] + sentence, self.requires_weekly):
            raise ModelError('模型将大小周误当成每周双休，已拦截且未保存。大小周包含单休周，请以岗位卡核对结果为准。',
                             502, 'inconsistent_schedule_claim')

    def feed(self, value: str) -> list[str]:
        self.pending += value
        output = []
        while self.pending:
            if not self.active:
                words = ALTERNATING + (PREDICATES if any(w in self.history[-120:] for w in ALTERNATING) else ())
                positions = [self.pending.find(w) for w in words if w in self.pending]
                if positions:
                    first = min(positions)
                    if first:
                        output.append(self.pending[:first])
                        self.history = (self.history + self.pending[:first])[-240:]
                    self.pending, self.active = self.pending[first:], True
                else:
                    keep = max((n for w in words for n in range(1, len(w)) if self.pending.endswith(w[:n])), default=0)
                    safe = self.pending[:-keep] if keep else self.pending
                    self.pending = self.pending[-keep:] if keep else ''
                    if safe:
                        output.append(safe)
                        self.history = (self.history + safe)[-240:]
                    break
            boundary = END.search(self.pending)
            if boundary is None:
                if len(self.pending) > 1600:
                    raise ModelError('排班说明过长，无法完成一致性校核，本段未保存。', 502, 'invalid_stream')
                break
            sentence, self.pending = self.pending[:boundary.end()], self.pending[boundary.end():]
            self._validate(sentence)
            output.append(sentence)
            self.history = (self.history + sentence)[-240:]
            self.active = False
        return output

    def finish(self) -> str:
        self._validate(self.pending)
        tail, self.pending = self.pending, ''
        return tail
