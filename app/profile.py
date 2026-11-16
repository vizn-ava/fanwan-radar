"""Conservative resume fact extraction. Interactive chat uses the real LLM client."""
from __future__ import annotations

import re
from .models import Profile
from .matching import detect_skills


def chinese_years(text: str) -> str:
    digits = {'零': 0, '一': 1, '二': 2, '两': 2, '三': 3, '四': 4,
              '五': 5, '六': 6, '七': 7, '八': 8, '九': 9}

    def convert(match):
        word = match[1]
        if '十' in word:
            first, last = word.split('十', 1)
            number = digits.get(first, 1) * 10 + digits.get(last, 0)
        else:
            number = digits.get(word)
        return str(number) + '年' if number is not None else match[0]

    return re.sub(r'([零一二两三四五六七八九十]{1,3})\s*年', convert, text)


def llm_configured() -> bool:
    from .model_config import public_settings
    return public_settings()['configured']


def parse_profile(text: str, old: Profile, *, resume: bool = False) -> tuple[Profile, list[str]]:
    if not resume:
        raise ValueError('聊天画像变更需通过已配置模型并校验本轮依据；不使用本地规则冒充模型')
    patch = {'resume_text': text[:60000]}
    changed = ['简历正文']
    # An in-progress degree is not silently promoted to a completed qualification.
    education_lines = [line for line in re.split(r'[\n。；]', text)
                       if not re.search(r'在读|在校|预计.*毕业|求职要求|岗位要求|任职要求|期望', line)]
    educations = re.findall(r'博士|硕士|本科|大专|高中|中专', '\n'.join(education_lines))
    if educations:
        order = ['中专', '高中', '大专', '本科', '硕士', '博士']
        patch['education'] = max(educations, key=order.index)
        changed.append('学历（请核对）')
    normalized = chinese_years(text)
    match = re.search(r'(?:工作|从业|累计工作)\s*(?:经验)?\s*(\d+(?:\.\d+)?)\s*年', normalized)
    if not match:
        match = re.search(r'(\d+(?:\.\d+)?)\s*年(?:的)?(?:(?:相关|客服|运营|开发|销售)\s*)?(?:工作)?经验', normalized)
    if match and float(match[1]) <= 70:
        patch['experience_years'] = float(match[1])
        changed.append('经验（请核对）')
    factual = '\n'.join(line for line in text.splitlines()
                        if not re.search(r'期望|求职意向|希望|想学|岗位要求|任职要求', line))
    skills = detect_skills(factual)
    if skills:
        patch['skills'] = list(dict.fromkeys(old.skills + skills))[:60]
        changed.append('技能（请核对）')
    return Profile.model_validate(old.model_dump() | patch), changed


async def chat_reply(message: str, old: Profile) -> tuple[Profile, str, str]:
    """Legacy Python entry point; HTTP chat supplies persisted multi-turn history."""
    from .llm import generate_reply
    result = await generate_reply({'profile': old, 'messages': [], 'revision': 0}, message, [])
    return Profile.model_validate(old.model_dump() | result['patch']), result['reply'], 'llm'
