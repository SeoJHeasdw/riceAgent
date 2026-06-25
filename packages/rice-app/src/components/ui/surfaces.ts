/**
 * 팝업·토스트가 공유하는 표면(surface) 스타일 — 디자인 시스템 단일 출처.
 *
 * 그동안 Modal·ConfirmDialog가 backdrop/표면 className을 각자 복붙해서
 * 투명도·blur·radius·그림자·등장 모션이 조금씩 어긋날 여지가 있었다. 여기서
 * 한 번만 정의하고 모든 팝업/토스트가 끌어쓰면 톤 조정이 한 곳에서 끝난다.
 *
 * className 문자열만 노출하므로(컴포넌트가 아님) 호출부에서 글로우·링 같은
 * 상태별 클래스를 자유롭게 합성할 수 있다.
 */

/** 다이얼로그 뒤 어두운 backdrop — 클릭 시 닫힘은 호출부에서 핸들러로. */
export const dialogBackdropCls =
  'absolute inset-0 bg-black/60 backdrop-blur-sm animate-fade-in';

/** 다이얼로그 본체 표면 — layer-01 카드 위에 떠 있는 깊은 그림자 + 라운드. */
export const dialogSurfaceCls =
  'bg-layer-01 border border-border-subtle-01 rounded-2xl shadow-overlay animate-scale-in';

/**
 * 토스트용 밝은 elevated 글래스 표면.
 *
 * 기존 토스트는 bg-layer-01/80 으로 스킬 카드(layer-01, #262626)와 톤이 겹쳐
 * "떠 있는 알림"으로 읽히지 않았다. 표면을 layer-02(#393939)로 한 단계 올리고
 * 강한 테두리·그림자로 카드 위에 분명히 떠 보이게 한다. 글로우/링/등장·퇴장
 * 애니메이션은 호출부에서 그대로 합성한다.
 */
export const floatingSurfaceCls =
  'bg-layer-02/85 backdrop-blur-xl border border-border-strong-01/60 shadow-elevation-3';

// Made with Bob
