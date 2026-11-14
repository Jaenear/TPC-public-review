"""Bounded TPC caption profile, not a general CSS/region WebVTT processor.

UTF-8, NOTE, cue IDs, multiline captions, standard inline markup/timestamps and
common cue settings. STYLE/REGION are explicitly rejected. Native browser track
loading is verified separately; timing validation is not a content/rights review.
"""

import re
from .core import ApiError

MAX_BYTES = 1024 * 1024
MAX_CUES = 10000
PROFILE = "TPC-VTT-1"
TIME = r"(?:[0-9]{2,5}:)?[0-5][0-9]:[0-5][0-9]\.[0-9]{3}"
ENT = re.compile(r"&(amp|lt|gt|nbsp|lrm|rlm|#[0-9]{1,7}|#x[0-9a-fA-F]{1,6});")
# RFC5646 well-formedness; no runtime network/IANA registry lookup.
LANG = re.compile(
    r"(?:[a-z]{2,3}(?:-[a-z]{3}){0,3}|[a-z]{4}|[a-z]{5,8})(?:-[a-z]{4})?(?:-(?:[a-z]{2}|[0-9]{3}))?(?:-(?:[a-z0-9]{5,8}|[0-9][a-z0-9]{3}))*(?:-[0-9a-wy-z](?:-[a-z0-9]{2,8})+)*(?:-x(?:-[a-z0-9]{1,8})+)?"
)
GRANDFATHERED = set(
    "en-gb-oed i-ami i-bnn i-default i-enochian i-hak i-klingon i-lux i-mingo i-navajo i-pwn i-tao i-tay i-tsu sgn-be-fr sgn-be-nl sgn-ch-de art-lojban cel-gaulish no-bok no-nyn zh-guoyu zh-hakka zh-min zh-min-nan zh-xiang".split()
)


def invalid(note):
    raise ApiError(400, "WebVTT: " + note)


def language(value):
    if not isinstance(value, str) or not 1 <= len(value) <= 63:
        invalid("언어 태그를 확인해주세요.")
    value = value.lower()
    if value in GRANDFATHERED or re.fullmatch(r"x(?:-[a-z0-9]{1,8})+", value):
        return value
    if not LANG.fullmatch(value):
        invalid("ko, en, ko-KR 같은 언어 태그를 확인해주세요.")
    parts = value.split("-")
    variants = set()
    extensions = set()
    in_extension = False
    for part in parts[1:]:
        if part == "x":
            break
        if len(part) == 1:
            if part in extensions:
                invalid("언어 확장 태그가 중복됐습니다.")
            extensions.add(part)
            in_extension = True
        elif not in_extension and (
            len(part) >= 5 or (len(part) == 4 and part[0].isdigit())
        ):
            if part in variants:
                invalid("언어 변형 태그가 중복됐습니다.")
            variants.add(part)
    return value


def seconds(value):
    if not re.fullmatch(TIME, value):
        invalid("시간은 mm:ss.mmm 또는 hh:mm:ss.mmm 형식이어야 합니다.")
    parts = value.split(":")
    tail = float(parts[-1])
    return tail + int(parts[-2]) * 60 + (int(parts[0]) * 3600 if len(parts) == 3 else 0)


def percentage(value):
    return bool(
        re.fullmatch(r"[0-9]{1,3}(?:\.[0-9]+)?%", value)
        and 0 <= float(value[:-1]) <= 100
    )


def settings(value):
    seen = set()
    for token in re.split(r"[ \t]+", value.strip(" \t")) if value.strip(" \t") else []:
        if ":" not in token:
            invalid("자막 위치 설정을 확인해주세요.")
        key, item = token.split(":", 1)
        if key in seen:
            invalid("같은 위치 설정을 중복 지정할 수 없습니다.")
        seen.add(key)
        parts = item.split(",")
        ok = False
        if key == "align":
            ok = item in ("start", "center", "end", "left", "right")
        elif key == "vertical":
            ok = item in ("rl", "lr")
        elif key == "size":
            ok = percentage(item)
        elif key == "line":
            ok = (
                len(parts) <= 2
                and (
                    percentage(parts[0])
                    or bool(re.fullmatch(r"-?[0-9]{1,6}", parts[0]))
                )
                and (len(parts) == 1 or parts[1] in ("start", "center", "end"))
            )
        elif key == "position":
            ok = (
                len(parts) <= 2
                and percentage(parts[0])
                and (
                    len(parts) == 1 or parts[1] in ("line-left", "center", "line-right")
                )
            )
        if not ok:
            invalid(
                "지원하지 않거나 잘못된 자막 위치 설정입니다. REGION 설정은 지원하지 않습니다."
            )


def cue_text(value, start, end):
    if "-->" in value:
        invalid("자막 본문에는 시간 화살표를 넣을 수 없습니다.")
    stack = []
    at = 0
    previous = start
    while at < len(value):
        ch = value[at]
        if ch == "&":
            match = ENT.match(value, at)
            if not match:
                invalid("본문의 & 문자는 &amp;처럼 이스케이프해주세요.")
            entity = match[1]
            if entity.startswith("#"):
                cp = int(entity[2:], 16) if entity.startswith("#x") else int(entity[1:])
                if cp == 0 or cp > 0x10FFFF or 0xD800 <= cp <= 0xDFFF:
                    invalid("잘못된 문자 참조입니다.")
            at = match.end()
            continue
        if ch != "<":
            at += 1
            continue
        close = value.find(">", at + 1)
        if close < 0:
            invalid("본문의 < 문자 또는 태그를 확인해주세요.")
        tag = value[at + 1 : close]
        if re.fullmatch(TIME, tag):
            current = seconds(tag)
            if not previous < current < end:
                invalid("본문 시간 표시는 구간 안에서 증가해야 합니다.")
            previous = current
        elif tag.startswith("/"):
            name = tag[1:]
            if name == "ruby" and stack and stack[-1] == "rt":
                stack.pop()
            if not stack or stack.pop() != name:
                invalid("자막 태그의 열고 닫는 순서를 확인해주세요.")
        else:
            match = re.fullmatch(
                r"(b|i|u|ruby|rt|c|v|lang)((?:\.[^\s.<>]+)*)(?:[ \t]+([^<>]+))?", tag
            )
            if not match:
                invalid("자막 본문에는 표준 자막 태그만 사용할 수 있습니다.")
            name, _, annotation = match.groups()
            if (name in ("v", "lang")) != bool(annotation):
                invalid("화자/언어 태그의 주석을 확인해주세요.")
            if name == "lang":
                language(annotation)
            if name == "rt":
                if stack and stack[-1] == "rt":
                    stack.pop()
                if not stack or stack[-1] != "ruby":
                    invalid("rt 태그는 ruby 안에서 사용해주세요.")
            stack.append(name)
        at = close + 1
    if stack == ["v"]:
        stack = []
    if stack:
        invalid("닫히지 않은 자막 태그가 있습니다.")


def validate(body, duration=None):
    if not isinstance(body, bytes) or not 8 <= len(body) <= MAX_BYTES:
        invalid("자막 파일은 UTF-8 WebVTT 1MB 이내로 준비해주세요.")
    try:
        text = body.decode("utf-8-sig").replace("\r\n", "\n").replace("\r", "\n")
    except UnicodeError:
        invalid("UTF-8로 저장한 WebVTT 파일이 필요합니다.")
    if any(ord(ch) < 32 and ch not in "\t\n" for ch in text):
        invalid("지원하지 않는 제어 문자가 있습니다.")
    lines = text.split("\n")
    if (
        not re.fullmatch(r"WEBVTT(?:[ \t][^\n]*)?", lines[0])
        or "-->" in lines[0]
        or len(lines) < 2
        or lines[1].strip()
    ):
        invalid("WEBVTT 헤더와 뒤의 빈 줄을 확인해주세요.")
    blocks = re.split(r"\n[ \t]*\n", "\n".join(lines[2:]).strip("\n"))
    cues = []
    prior = -1
    maximum = 0
    for block in blocks:
        if not block.strip():
            continue
        rows = block.split("\n")
        if re.fullmatch(r"NOTE(?:[ \t].*)?", rows[0]):
            if "-->" in block:
                invalid("NOTE 주석 안에는 시간 화살표를 넣을 수 없습니다.")
            continue
        if rows[0] in ("STYLE", "REGION"):
            invalid(
                "TPC-VTT-1은 STYLE/REGION 블록을 지원하지 않습니다. 일반 자막으로 내보내주세요."
            )
        timing = 0 if "-->" in rows[0] else 1
        if timing >= len(rows) or (timing and ("-->" in rows[0] or len(rows[0]) > 200)):
            invalid("자막 구간 또는 식별자를 확인해주세요.")
        match = re.fullmatch(
            r"(" + TIME + r")[ \t]+-->[ \t]+(" + TIME + r")(?:[ \t]+(.*))?",
            rows[timing],
        )
        if not match:
            invalid("자막 시작/종료 시간 줄을 확인해주세요.")
        start, end = seconds(match[1]), seconds(match[2])
        if start < prior or not start < end or end > 86400:
            invalid("시작은 순서대로, 종료는 시작 뒤의 24시간 이내여야 합니다.")
        if duration and end > duration + 1:
            invalid("자막 종료가 영상 원본의 길이를 넘습니다.")
        settings(match[3] or "")
        value = "\n".join(rows[timing + 1 :])
        if not value.strip():
            invalid("각 자막 구간에 본문을 입력해주세요.")
        cue_text(value, start, end)
        cues.append({"start": start, "end": end, "text": value})
        prior = start
        maximum = max(maximum, end)
        if len(cues) > MAX_CUES:
            invalid("자막은 10,000개 구간 이내로 준비해주세요.")
    if not cues:
        invalid("최소 한 개의 자막 구간이 필요합니다.")
    return {
        "profile": PROFILE,
        "cueCount": len(cues),
        "endSeconds": maximum,
        "cues": cues,
    }
