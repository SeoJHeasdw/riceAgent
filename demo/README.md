# 데모 촬영

소개 영상을 찍을 때 RICE를 어떻게 띄우는지와, 그때만 달라지는 것들.

## 데모 모드

`RICE_DEMO=1`로 띄우면 RICE는 데모 모드로 뜬다. 켜져 있지 않으면 아무것도 달라지지
않는다. 아는 코드는 두 곳뿐이다 — `rice-app/electron/demo.ts`(main)와
`rice-app/src/lib/demo.ts`(렌더러).

| 켜면 | 왜 |
| --- | --- |
| 개발 서버 대신 **빌드된 화면**을 읽고 DevTools를 열지 않는다 | 제품에 없는 것이 소개 영상에 남으면 안 된다 |
| `<html data-demo="<배율>">` 표식이 붙는다 | 촬영이 데모 모드로 떴는지 확인한다 |
| 3D·CSS 애니메이션과 온보딩 단계가 **¼ 속도**로 흐른다 | 4K에서 3D는 초당 9장쯤 그린다. ¼로 찍어 편집에서 4배로 감으면 1초에 36장이 되어 부드럽다 |

배율은 `RICE_DEMO_TIME_SCALE`로 바꾼다(0 초과 1 이하, 기본 0.25). 1이면 실제 속도다.
데모에서는 `window.__riceDemo`로 그 시계를 볼 수 있어, 배율이 실제로 걸렸는지 잰다.

```bash
npm --prefix rice-app run build:electron        # 빌드된 화면을 만든다
RICE_DEMO=1 rice-app/node_modules/.bin/electron rice-app
```

## 시나리오

`demo/scenarios/*.json`은 촬영 엔진(`javis/local-tts-engine`)이 읽는다. 엔진은 RICE를
모르고, 앱의 사정은 이 파일에만 있다. 실행은 엔진 쪽에서 한다.

```bash
RICE_DEMO_SCENARIO="$PWD/demo/scenarios/rice-first-run.json"
cd ../../javis/local-tts-engine
npm run demo -- record "$RICE_DEMO_SCENARIO"
```

느리게 그려지는 장면(온보딩 시네마틱)은 시나리오의 장면에 `"timeScale": 0.25`를 적는다.
그 사실을 아는 것은 앱뿐이고, 편집은 그 값을 보고 장면을 통째로 4배로 되돌린다.
적지 않으면 실제 속도로 찍힌 장면으로 보아 기다림만 감는다.

첫 실행 시나리오의 온보딩·투어는 `camera: "overview"`로 전체 구도를 지킨다. 아래쪽
진행 버튼을 확대하면 위 제목과 3D 구성이 잘리기 때문이다. `textFrom`은 해당 온보딩
단계·투어를 가리킨다. 촬영 엔진은 화면을 떠나기 직전의 글도 보존한다.

`{scenario}`는 이 폴더, `{work}`는 그 촬영의 작업 폴더로 풀린다. `RICE_HOME`을 작업
폴더로 두므로 촬영은 실제 설정·대화 기록을 건드리지 않는다.
