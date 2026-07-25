"""core/tokens.py — 언어 인식 토큰 추정 + 세션 캘리브레이션.

WHY THIS FILE EXISTS
------------------------------------------------------
지금까지 컨텍스트 예산은 전부 `len(text) / 4` 였다. 영어엔 맞지만 한국어/일본어/
중국어(CJK)에는 **거짓 안전판**이다: Claude 토크나이저에서 한글은 대략 1글자당
1토큰 안팎이라, `len/4`는 실제 토큰을 2~4배 **과소평가**한다. 96k로 "관리했다"고
믿은 컨텍스트가 실제로는 200k라 provider 한도에서 먼저 터진다.

두 층위로 고친다:
  1. **CJK 가중 휴리스틱**(`estimate_tokens`) — 문자를 CJK/그 외로 분류해 서로
     다른 글자당 토큰 비율을 적용한다. 보수적으로(약간 과대추정) 잡아, 틀리더라도
     안전한 방향(더 일찍 압축)으로 틀리게 한다.
  2. **세션 캘리브레이션**(`TokenCalibrator`) — provider가 실제 `input_tokens`를
     돌려주면 그것으로 휴리스틱↔실측 비율을 EMA로 학습해 이후 추정을 보정한다.
     실측이 없으면(echo/오프라인) 휴리스틱 그대로.

순수 함수(`estimate_tokens`)와 상태 객체(`TokenCalibrator`)를 나눈 이유: 예산
계산 대부분은 상태가 필요 없고(리뷰/프리페치), 캘리브레이션은 메인 루프에서만
실측 피드백을 받기 때문이다.
"""

from __future__ import annotations

import threading

# 글자당 토큰 비율(보수적 — 안전한 방향으로 약간 과대추정).
#   CJK 1글자 ≈ 1토큰 (Claude 기준 대략 0.7~1.5; 과대 쪽인 1.0을 택함).
#   그 외(라틴/숫자/공백) 4글자 ≈ 1토큰.
_CJK_TOKENS_PER_CHAR = 1.0
_OTHER_TOKENS_PER_CHAR = 0.25

# 캘리브레이션 비율 안전 범위 — 실측 노이즈로 폭주하지 않게 클램프.
_RATIO_MIN = 0.5
_RATIO_MAX = 4.0
_EMA_ALPHA = 0.3  # 새 관측 가중치. 낮을수록 안정적, 높을수록 빠르게 적응.


def _is_cjk(ch: str) -> bool:
    """CJK(한글/한자/가나 등 폭넓은 표의·음절 문자)면 True. 이 문자들은 BPE에서
    라틴보다 훨씬 촘촘하게 토큰화된다(글자당 토큰이 크다).

    `estimate_tokens`는 더 이상 이 함수를 문자마다 부르지 않는다(아래 참조) —
    코드포인트 분류의 **정본 정의**로 남아 테스트/문서가 참조한다."""
    o = ord(ch)
    return (
        0x1100 <= o <= 0x11FF      # Hangul Jamo
        or 0x2E80 <= o <= 0x2FDF   # CJK Radicals / Kangxi
        or 0x3040 <= o <= 0x30FF   # Hiragana + Katakana
        or 0x3100 <= o <= 0x312F   # Bopomofo
        or 0x3130 <= o <= 0x318F   # Hangul Compatibility Jamo
        or 0x3400 <= o <= 0x4DBF   # CJK Ext A
        or 0x4E00 <= o <= 0x9FFF   # CJK Unified
        or 0xA960 <= o <= 0xA97F   # Hangul Jamo Extended-A
        or 0xAC00 <= o <= 0xD7A3   # Hangul Syllables
        or 0xF900 <= o <= 0xFAFF   # CJK Compatibility Ideographs
        or 0xFF00 <= o <= 0xFFEF   # Fullwidth forms
        or 0x20000 <= o <= 0x3FFFF # CJK Ext B+ (astral)
    )


def estimate_tokens(text: str) -> int:
    """언어 인식 토큰 추정(순수 함수). CJK 글자와 그 외를 나눠 가중합한다.

    빈 문자열은 0. 결과는 최소 1(비지 않은 입력) — 0-비용 메시지가 예산에서
    사라져 wire 정합성 계산이 어긋나는 걸 막는다.

    구현(2026-07-25): 문자마다 `_is_cjk`를 부르는 파이썬 루프였고, 이게 렌더
    비용을 지배했다 — 컨텍스트 매니저가 반복마다 트랜스크립트 전체를 여러 번
    세므로(70KB당 19.5ms), **동기 호출인 `loop.py::_render`가 이벤트 루프를
    반복마다 수십 ms씩 막아** SSE 토큰 flush 가 끊겼다. 이제 UTF-8 바이트
    길이 산술로 같은 값을 C 속도로 얻는다(70KB당 0.04ms):

        CJK(BMP) 문자는 UTF-8 3바이트 = 문자당 +2바이트.
        ASCII 는 1바이트 = +0. 따라서 (bytes - chars) // 2 가 CJK 글자 수다.

    ASCII·한글·한자·가나·혼합·JSON 에서 결과가 **비트 단위로 동일**하다(실측).
    갈리는 건 2바이트(라틴-1/키릴)와 4바이트(이모지, CJK Ext-B) 문자뿐이고,
    전부 **과대추정** 쪽이다 — 이 파일이 못박은 "틀리더라도 안전한 방향(더
    일찍 압축)"과 같은 방향이라 예산 안전성은 오히려 강해진다."""
    if not text:
        return 0
    n_chars = len(text)
    cjk = (len(text.encode("utf-8")) - n_chars) // 2
    other = n_chars - cjk
    est = cjk * _CJK_TOKENS_PER_CHAR + other * _OTHER_TOKENS_PER_CHAR
    return max(1, int(est + 0.5))


class TokenCalibrator:
    """휴리스틱 추정을 provider 실측으로 보정하는 스레드 안전 상태 객체.

    컨테이너에 싱글턴으로 산다. 메인 루프가 provider의 `input_tokens`를 받으면
    `observe(...)`로 비율을 갱신하고, 컨텍스트 매니저·프리페치는 `estimate(text)`
    (= 휴리스틱 × 학습된 비율)를 예산 계산에 쓴다.

    실측이 한 번도 없으면 비율=1.0이라 순수 휴리스틱과 동일하다(echo/오프라인
    폴백에서도 안전)."""

    def __init__(self) -> None:
        self._ratio = 1.0
        self._samples = 0
        self._lock = threading.Lock()

    @property
    def ratio(self) -> float:
        return self._ratio

    @property
    def samples(self) -> int:
        return self._samples

    def estimate(self, text: str) -> int:
        return max(1, int(estimate_tokens(text) * self._ratio + 0.5)) if text else 0

    def estimate_context(self, system: str, messages_text: str) -> int:
        return self.estimate(system) + self.estimate(messages_text)

    def observe(self, heuristic_estimate: int, actual_tokens: int) -> None:
        """실측 토큰과 (같은 콘텐츠의) 순수 휴리스틱 추정을 비교해 비율을 EMA
        갱신한다. 잡음/영(0) 입력은 무시. best-effort — 캘리브레이션 실패가 턴을
        깨선 안 되므로 호출부는 예외를 감싼다."""
        if heuristic_estimate <= 0 or actual_tokens <= 0:
            return
        observed = actual_tokens / heuristic_estimate
        if observed < _RATIO_MIN:
            # 프리픽스 KV 캐시 히트: Ollama의 prompt_eval_count(Claude의
            # input_tokens도 동일)는 캐시에서 재사용된 프리픽스를 빼고 새로
            # 평가한 토큰만 센다 — 전체 컨텍스트의 실측이 아니다. 이를 하한으로
            # 클램프해 EMA에 넣으면 비율이 _RATIO_MIN으로 끌려가 예산이 반토막
            # 과소평가되고, 초과분을 Ollama가 소리 없이 앞에서 자른다. 설계상
            # 실제 토크나이저 비율은 [_RATIO_MIN, _RATIO_MAX] 안이므로 하한 미만
            # 관측은 캐시 아티팩트로 보고 통째로 버린다.
            return
        observed = min(_RATIO_MAX, observed)
        with self._lock:
            if self._samples == 0:
                self._ratio = observed
            else:
                self._ratio = (1 - _EMA_ALPHA) * self._ratio + _EMA_ALPHA * observed
            self._ratio = max(_RATIO_MIN, min(_RATIO_MAX, self._ratio))
            self._samples += 1


# 컨텍스트 길이 초과 에러 탐지 — provider별 메시지 문구가 다르므로 보수적으로
# 흔한 표현을 모은다. 매칭되면 루프가 더 강하게 압축해 1회 재시도한다(P0).
_CONTEXT_LENGTH_MARKERS = (
    "prompt is too long",
    "context length",
    "context_length",
    "maximum context",
    "too many tokens",
    "input is too long",
    "exceeds the maximum",
    "reduce the length",
    "context window",
    "max_tokens",
    "413",
)


def is_context_length_error(exc: BaseException) -> bool:
    """예외가 '컨텍스트/프롬프트가 너무 길다' 부류면 True — 더 강한 압축 후
    1회 재시도의 트리거."""
    text = str(exc).lower()
    return any(marker in text for marker in _CONTEXT_LENGTH_MARKERS)
