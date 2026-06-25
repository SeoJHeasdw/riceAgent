import { Model, ModelSettings } from '../components/carbon/ModelSettingsPanel';

/**
 * Mock data for development and testing.
 * Models/settings for the chat model picker, and the skill/plugin marketplaces.
 * (Learning-loop content lives in ./learningData.)
 */

// Available models
export const mockModels: { saas: Model[]; local: Model[] } = {
  saas: [
    {
      id: 'claude-sonnet-4-5',
      name: 'Claude Sonnet 4.5',
      description: 'Anthropic API · 고품질 · prompt caching',
      badge: '권장',
    },
  ],
  local: [
    {
      id: 'qwen3.6-27b',
      name: 'Qwen3.6 27B',
      description: 'Ollama · 로컬 · 고성능',
      badge: '오프라인',
    },
    {
      id: 'qwen-3.5-9b',
      name: 'Qwen3.5 9B',
      description: 'Ollama · 로컬 · 균형',
      badge: '오프라인',
    },
    {
      id: 'qwen3-1.7b',
      name: 'Qwen3 1.7B',
      description: 'Ollama · 로컬 · 경량 · 빠름',
      badge: '오프라인',
    },
  ],
};

// Default model settings
export const mockModelSettings: ModelSettings = {
  temperature: 0.7,
  maxTokens: 2048,
  contextLength: '32K',
  registeredModels: [],
  endpoint: 'http://localhost:11434',
  userModelingEnabled: true,
  localStorageOnly: false,
};

// Skill categories
export const skillCategories = [
  'All',
  '데이터 분석',
  '자동화',
  '연동',
  '개발',
  '커뮤니케이션',
  '생산성',
];

// 플러그인 카테고리
export const pluginCategories = [
  'WXO',
  '영업',
  'HR',
  '재무',
  '마케팅',
  'IT 운영',
];

// 스킬 마켓 샘플 데이터
export const mockSkills = [
  {
    id: 'skill-1',
    name: 'Excel 데이터 분석기',
    description: 'Excel 파일을 자동으로 분석하고 차트와 요약본을 포함한 인사이트를 생성합니다',
    version: '2.1.0',
    author: '박지현',
    team: '데이터 분석',
    category: '데이터 분석',
    tags: ['excel', '데이터', '분석'],
    downloads: 12847,
    rating: 4.8,
    points: 3200,
    installed: false,
    enabled: false,
  },
  {
    id: 'skill-2',
    name: 'Slack 알림 발송',
    description: '트리거와 이벤트를 기반으로 Slack 채널에 자동 알림을 전송합니다',
    version: '1.5.2',
    author: '이민준',
    team: 'DevOps',
    category: '커뮤니케이션',
    tags: ['slack', '알림', '자동화'],
    downloads: 8934,
    rating: 4.6,
    points: 1850,
    installed: true,
    enabled: true,
  },
  {
    id: 'skill-3',
    name: 'PDF 보고서 생성기',
    description: '템플릿에서 동적 데이터를 삽입하여 전문적인 PDF 보고서를 만듭니다',
    version: '3.0.1',
    author: '정수아',
    team: '비즈니스 인텔리전스',
    category: '생산성',
    tags: ['pdf', '보고서', '템플릿'],
    downloads: 15623,
    rating: 4.9,
    points: 4500,
    installed: false,
    enabled: false,
    files: [
      {
        path: 'SKILL.md',
        language: 'markdown' as const,
        content: `---
name: pdf-report-generator
description: Create professional PDF reports from templates with dynamic data insertion
version: 3.0.1
---

# PDF Report Generator

Generate polished, branded PDF reports by merging structured data into reusable
templates. Ideal for recurring business reports, invoices, and data summaries.

## When to use

Use this skill whenever you need a print-ready PDF from structured data — for
example monthly KPIs, customer invoices, or audit summaries. Call it with
\`/pdfreportgenerator\` or let RICE invoke it automatically.

## How it works

1. Pick a template from \`templates/\` (or provide your own HTML/Handlebars).
2. Run \`scripts/render.sh\` with your data file (JSON or CSV).
3. The skill injects the data, renders to PDF, and returns the file.

## Inputs

- \`template\` — template name or path
- \`data\` — JSON/CSV with the values to insert
- \`output\` — destination path (defaults to \`./report.pdf\`)

## Tags

- pdf
- reports
- templates
`,
      },
      {
        path: 'README.md',
        language: 'markdown' as const,
        content: `# PDF Report Generator

> Create professional PDF reports from templates with dynamic data insertion.

Maintained by 정수아 · 비즈니스 인텔리전스.

## Installation

Install from the Skill market, then call \`/pdfreportgenerator\` in chat.

## Quick start

\`\`\`
/pdfreportgenerator template=quarterly data=q3.json
\`\`\`

## Files

- \`SKILL.md\` — skill definition and instructions
- \`scripts/render.sh\` — renders a template + data into a PDF
- \`templates/invoice.html\` — sample invoice template
- \`LICENSE.txt\` — license terms
`,
      },
      {
        path: 'scripts/render.sh',
        language: 'shell' as const,
        content: `#!/usr/bin/env bash
set -euo pipefail

# PDF Report Generator — render a template with data into a PDF
TEMPLATE="\${1:-templates/invoice.html}"
DATA="\${2:-data.json}"
OUTPUT="\${3:-report.pdf}"

echo "Rendering $TEMPLATE with $DATA → $OUTPUT"

# Merge data into the template and print to PDF via headless Chromium
node ./bin/merge.js "$TEMPLATE" "$DATA" > .out.html
chromium --headless --print-to-pdf="$OUTPUT" .out.html
rm -f .out.html

echo "Done: $OUTPUT"
`,
      },
      {
        path: 'templates/invoice.html',
        language: 'text' as const,
        content: `<!doctype html>
<html>
  <head><meta charset="utf-8" /><title>{{title}}</title></head>
  <body>
    <h1>Invoice #{{invoice.number}}</h1>
    <p>Billed to: {{customer.name}}</p>
    <table>
      {{#each lineItems}}
      <tr><td>{{this.label}}</td><td>{{this.amount}}</td></tr>
      {{/each}}
    </table>
    <strong>Total: {{invoice.total}}</strong>
  </body>
</html>
`,
      },
      {
        path: 'LICENSE.txt',
        language: 'text' as const,
        content: `MIT License

Copyright (c) 2026 정수아

Permission is hereby granted, free of charge, to any person obtaining a copy of
this software and associated documentation files, to deal in the software
without restriction. THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY
KIND.
`,
      },
    ],
  },
  {
    id: 'skill-4',
    name: 'API 커넥터',
    description: '인증 및 데이터 변환 기능을 갖춘 REST API에 연결합니다',
    version: '2.3.0',
    author: '김도현',
    team: '연동',
    category: '연동',
    tags: ['api', 'rest', '연동'],
    downloads: 10234,
    rating: 4.7,
    points: 2900,
    installed: false,
    enabled: false,
  },
  {
    id: 'skill-5',
    name: '이메일 캠페인 관리자',
    description: '추적 및 분석 기능과 함께 이메일 캠페인을 관리하고 자동화합니다',
    version: '1.8.0',
    author: '최유진',
    team: '마케팅',
    category: '자동화',
    tags: ['이메일', '마케팅', '캠페인'],
    downloads: 7456,
    rating: 4.5,
    points: 1600,
    installed: false,
    enabled: false,
  },
  {
    id: 'skill-6',
    name: 'DB 쿼리 빌더',
    description: '비주얼 인터페이스와 결과 시각화로 SQL 쿼리를 작성하고 실행합니다',
    version: '2.0.0',
    author: '임재원',
    team: '데이터 엔지니어링',
    category: '개발',
    tags: ['sql', '데이터베이스', '쿼리'],
    downloads: 9876,
    rating: 4.8,
    points: 2400,
    installed: true,
    enabled: false,
  },
  {
    id: 'skill-7',
    name: '캘린더 동기화',
    description: '여러 캘린더 플랫폼 간 이벤트를 자동으로 동기화합니다',
    version: '1.2.5',
    author: '한소희',
    team: '생산성',
    category: '생산성',
    tags: ['캘린더', '동기화', '일정'],
    downloads: 6543,
    rating: 4.4,
    points: 1200,
    installed: false,
    enabled: false,
  },
  {
    id: 'skill-8',
    name: '코드 포매터',
    description: '사용자 정의 규칙으로 다양한 프로그래밍 언어의 코드를 정리하고 통일합니다',
    version: '3.1.0',
    author: '오성민',
    team: '개발자 도구',
    category: '개발',
    tags: ['코드', '포매팅', '린팅'],
    downloads: 11234,
    rating: 4.7,
    points: 2800,
    installed: false,
    enabled: false,
  },
  {
    id: 'skill-9',
    name: '이미지 최적화',
    description: '화질을 유지하면서 웹용 이미지를 압축하고 최적화합니다',
    version: '2.2.0',
    author: '신예진',
    team: '웹 개발',
    category: '생산성',
    tags: ['이미지', '최적화', '압축'],
    downloads: 8765,
    rating: 4.6,
    points: 2100,
    installed: false,
    enabled: false,
  },
  {
    id: 'skill-10',
    name: '작업 스케줄러',
    description: '유연한 타이밍 옵션으로 반복 작업을 예약하고 자동화합니다',
    version: '1.9.0',
    author: '강태양',
    team: '자동화',
    category: '자동화',
    tags: ['스케줄링', '자동화', '작업'],
    downloads: 13456,
    rating: 4.8,
    points: 3500,
    installed: false,
    enabled: false,
  },
];

// 플러그인 마켓 샘플 데이터
export const mockPlugins = [
  {
    id: 'plugin-1',
    name: 'Watson Assistant 전문가',
    description: 'Watson Assistant 전문 지식으로 대화형 AI 솔루션을 구축하고 관리합니다',
    version: '2.0.0',
    author: 'Watson 팀',
    provider: 'IBM Watson',
    category: 'WXO',
    tags: ['watson', 'ai', '챗봇'],
    skillCount: 6,
    skills: [
      { command: 'watson_intent_design', desc: '발화 예시로 인텐트·엔티티를 자동 구성' },
      { command: 'watson_dialog_flow', desc: '노드 기반 다이얼로그 흐름 설계' },
      { command: 'watson_webhook_action', desc: 'Webhook으로 외부 API 호출 액션 구성' },
      { command: 'watson_search_integrate', desc: 'Conversational Search 검색 연동' },
      { command: 'watson_test_coverage', desc: '발화 세트로 인식률·커버리지 점검' },
      { command: 'watson_deploy_assistant', desc: '채널 게시·버전 롤아웃' },
    ],
    gains: '대화 설계 · 인텐트 학습 · 다이얼로그 관리 · 연동 패턴',
    useCases: ['고객 데모', '솔루션 설계', '문제 해결'],
    connectors: [
      { name: 'Watson Assistant API', desc: '어시스턴트·세션·메시지 호출' },
      { name: 'Webhook 엔드포인트', desc: '다이얼로그에서 외부 시스템 호출' },
      { name: 'Slack', desc: '채널로 봇 응답 연동' },
    ],
    knowledge: [
      { name: 'Watson Assistant 제품 문서', desc: '어시스턴트 구성·배포 공식 가이드', meta: '280 문서 · v2.0' },
      { name: '대화 설계 패턴집', desc: '인텐트·엔티티·다이얼로그 모범 사례', meta: '64 문서' },
      { name: '연동 레시피', desc: 'Webhook·검색 연동 구성 예제', meta: '갱신됨' },
    ],
    installed: true,
    enabled: true,
    icon: 'watson',
  },
  {
    id: 'plugin-2',
    name: 'Salesforce 연동 전문가',
    description: '엔터프라이즈 배포를 위한 Salesforce 연동 패턴과 모범 사례를 마스터합니다',
    version: '1.5.0',
    author: '영업 팀',
    provider: 'IBM Sales',
    category: '영업',
    tags: ['salesforce', 'crm', '연동'],
    skillCount: 5,
    skills: [
      { command: 'sfdc_api_integrate', desc: 'REST·Bulk API 연동 코드 생성' },
      { command: 'sfdc_data_migrate', desc: '객체 매핑·대량 데이터 이행 점검' },
      { command: 'sfdc_custom_object', desc: '커스텀 오브젝트·관계 스키마 설계' },
      { command: 'sfdc_flow_automate', desc: 'Flow·트리거 기반 워크플로 구성' },
      { command: 'sfdc_soql_optimize', desc: 'SOQL 쿼리 성능 진단·개선' },
    ],
    gains: 'API 연동 · 데이터 마이그레이션 · 커스텀 오브젝트 · 워크플로 자동화',
    useCases: ['고객 온보딩', '시스템 연동', '데이터 동기화'],
    connectors: [
      { name: 'Salesforce REST API', desc: '레코드 조회·생성·수정' },
      { name: 'Salesforce Bulk API', desc: '대량 데이터 마이그레이션' },
      { name: 'OAuth 자격 증명', desc: '연결 앱 인증 토큰' },
    ],
    knowledge: [
      { name: 'Salesforce 연동 가이드', desc: 'API·인증·한도 모범 사례', meta: '190 문서' },
      { name: '객체 스키마 레퍼런스', desc: '표준·커스텀 오브젝트 필드 정의', meta: '갱신됨' },
      { name: '마이그레이션 플레이북', desc: '데이터 이행 단계·검증 체크리스트', meta: '32 문서' },
    ],
    installed: false,
    enabled: false,
    icon: 'cloud',
  },
  {
    id: 'plugin-3',
    name: 'HR 분석 전문가',
    description: '인력 데이터를 분석하고 HR 의사결정을 위한 인사이트를 생성합니다',
    version: '1.8.0',
    author: 'HR 분석 팀',
    provider: 'IBM HR',
    category: 'HR',
    tags: ['hr', '분석', '인력'],
    skillCount: 5,
    skills: [
      { command: 'hr_attrition_predict', desc: '이탈 위험군 스코어링 실행' },
      { command: 'hr_workforce_analyze', desc: '조직·인력 지표 집계·시각화' },
      { command: 'hr_performance_review', desc: '성과 평가 데이터 요약' },
      { command: 'hr_diversity_insight', desc: '구성·격차 다양성 분석' },
      { command: 'hr_compliance_check', desc: '인사 규정 위반 신호 탐지' },
    ],
    gains: '인력 분석 · 이탈 예측 모델링 · 성과 지표 · 다양성 인사이트',
    useCases: ['인재 계획', '성과 리뷰', '컴플라이언스'],
    connectors: [
      { name: 'Workday API', desc: '인사·조직 데이터 조회' },
      { name: 'HR 데이터 웨어하우스', desc: '인력 지표·이력 데이터' },
      { name: 'IBM HR DB', desc: '성과·근태 레코드' },
    ],
    knowledge: [
      { name: '인력 분석 방법론', desc: '이직·성과 지표 정의와 해석', meta: '120 문서' },
      { name: '이탈 예측 모델 문서', desc: '피처·임계값·검증 기준', meta: 'v1.8' },
      { name: 'HR 컴플라이언스 가이드', desc: '개인정보·다양성 보고 규정', meta: '갱신됨' },
    ],
    installed: false,
    enabled: false,
    icon: 'user-multiple',
  },
  {
    id: 'plugin-4',
    name: 'Cloud Pak for Data 전문가',
    description: '모범 사례로 Cloud Pak for Data 솔루션을 배포하고 관리합니다',
    version: '3.0.0',
    author: '클라우드 팀',
    provider: 'IBM Cloud',
    category: 'WXO',
    tags: ['cloud-pak', '데이터', 'kubernetes'],
    skillCount: 5,
    skills: [
      { command: 'cp4d_architect', desc: '노드·스토리지 아키텍처 설계' },
      { command: 'cp4d_deploy_automate', desc: '설치·업그레이드 자동화' },
      { command: 'cp4d_perf_tune', desc: '리소스·스케일링 성능 튜닝' },
      { command: 'cp4d_security_setup', desc: 'RBAC·네트워크 정책 구성' },
      { command: 'cp4d_troubleshoot_ocp', desc: 'OpenShift 워크로드 장애 진단' },
    ],
    gains: '아키텍처 설계 · 배포 자동화 · 성능 튜닝 · 보안 설정',
    useCases: ['온프레미스 배포', '마이그레이션', '최적화'],
    connectors: [
      { name: 'Kubernetes API', desc: '클러스터·워크로드 배포 제어' },
      { name: 'Cloud Pak 콘솔', desc: '서비스 인스턴스 프로비저닝' },
      { name: '컨테이너 레지스트리', desc: '이미지 풀·버전 관리' },
    ],
    knowledge: [
      { name: 'CP4D 배포 가이드', desc: '설치·업그레이드·HA 구성', meta: '410 문서 · v3.0' },
      { name: '아키텍처 레퍼런스', desc: '노드·스토리지·네트워크 설계', meta: '갱신됨' },
      { name: '성능 튜닝 핸드북', desc: '리소스·스케일링 운영 패턴', meta: '76 문서' },
    ],
    installed: true,
    enabled: false,
    icon: 'cloud-data',
  },
  {
    id: 'plugin-5',
    name: '마케팅 자동화 전문가',
    description: '분석 기능을 갖춘 마케팅 자동화 캠페인을 설계하고 실행합니다',
    version: '2.1.0',
    author: '마케팅 팀',
    provider: 'IBM Marketing',
    category: '마케팅',
    tags: ['마케팅', '자동화', '캠페인'],
    skillCount: 4,
    skills: [
      { command: 'mkt_campaign_design', desc: '세그먼트·여정 캠페인 설계' },
      { command: 'mkt_lead_scoring', desc: '행동 기반 리드 점수 규칙 생성' },
      { command: 'mkt_email_automate', desc: '이메일 발송 시퀀스 구성' },
      { command: 'mkt_roi_track', desc: '어트리뷰션·ROI 리포트' },
    ],
    gains: '캠페인 설계 · 리드 스코어링 · 이메일 자동화 · ROI 추적',
    useCases: ['캠페인 기획', '리드 육성', '분석'],
    connectors: [
      { name: 'Marketo API', desc: '캠페인·리드 객체 연동' },
      { name: '이메일 발송 게이트웨이', desc: '대량 발송·트래킹' },
      { name: 'Google Analytics 4', desc: '유입·전환 지표 수집' },
    ],
    knowledge: [
      { name: '캠페인 설계 플레이북', desc: '세그먼트·여정·A/B 테스트 패턴', meta: '88 문서' },
      { name: '리드 스코어링 모델', desc: '행동·속성 기반 점수 규칙', meta: 'v2.1' },
      { name: 'ROI 측정 가이드', desc: '어트리뷰션·KPI 정의', meta: '갱신됨' },
    ],
    installed: false,
    enabled: false,
    icon: 'chart-line',
  },
  {
    id: 'plugin-6',
    name: '재무 계획 전문가',
    description: '엔터프라이즈급 정확도로 재무 모델과 예측을 수립합니다',
    version: '1.6.0',
    author: '재무 팀',
    provider: 'IBM Finance',
    category: '재무',
    tags: ['재무', '계획', '예측'],
    skillCount: 4,
    skills: [
      { command: 'fin_model_build', desc: '예측 재무 모델 구성' },
      { command: 'fin_budget_plan', desc: '계정별 예산 편성 초안' },
      { command: 'fin_variance_analyze', desc: '예실 차이(분산) 분석' },
      { command: 'fin_risk_assess', desc: '민감도·시나리오 리스크 평가' },
    ],
    gains: '재무 모델링 · 예산 계획 · 분산 분석 · 리스크 평가',
    useCases: ['예산 계획', '예측', '보고'],
    connectors: [
      { name: 'SAP ERP', desc: '총계정·실적 데이터 조회' },
      { name: '재무 데이터 마트', desc: '예산·집행 내역' },
      { name: 'Planning Analytics (TM1)', desc: '큐브 기반 예측 모델' },
    ],
    knowledge: [
      { name: '재무 모델링 가이드', desc: '예측·시나리오 분석 방법론', meta: '140 문서' },
      { name: '예산 편성 표준', desc: '계정 체계·승인 절차', meta: '갱신됨' },
      { name: '리스크 평가 프레임워크', desc: '민감도·분산 분석 기준', meta: '36 문서' },
    ],
    installed: false,
    enabled: false,
    icon: 'currency-dollar',
  },
  {
    id: 'plugin-7',
    name: 'IT 운영 전문가',
    description: '자동화와 인사이트로 IT 인프라를 모니터링하고 관리합니다',
    version: '2.3.0',
    author: 'IT 운영 팀',
    provider: 'IBM IT',
    category: 'IT 운영',
    tags: ['모니터링', '자동화', '인프라'],
    skillCount: 5,
    skills: [
      { command: 'itops_infra_monitor', desc: '메트릭·대시보드 구성' },
      { command: 'itops_incident_respond', desc: '심각도 분류·런북 실행' },
      { command: 'itops_automate_script', desc: '운영 자동화 스크립트 생성' },
      { command: 'itops_capacity_plan', desc: '리소스 추세·용량 계획' },
      { command: 'itops_troubleshoot_ocp', desc: 'OpenShift 워크로드 장애 진단' },
    ],
    gains: '인프라 모니터링 · 인시던트 대응 · 자동화 스크립트 · 용량 계획',
    useCases: ['시스템 모니터링', '인시던트 관리', '자동화'],
    connectors: [
      { name: 'Prometheus', desc: '메트릭 수집·쿼리' },
      { name: 'PagerDuty', desc: '알림·온콜 에스컬레이션' },
      { name: 'ServiceNow', desc: '인시던트·변경 티켓 연동' },
    ],
    knowledge: [
      { name: '인프라 운영 런북', desc: '서비스별 대응 절차 모음', meta: '160 문서' },
      { name: '인시던트 대응 절차', desc: '심각도 분류·복구 단계', meta: '갱신됨' },
      { name: '용량 계획 가이드', desc: '리소스 추세·스케일링 기준', meta: '42 문서' },
    ],
    installed: false,
    enabled: false,
    icon: 'server',
  },
  {
    id: 'plugin-8',
    name: '보안 컴플라이언스 전문가',
    description: '엔터프라이즈 시스템 및 프로세스 전반의 보안 컴플라이언스를 보장합니다',
    version: '1.9.0',
    author: '보안 팀',
    provider: 'IBM Security',
    category: 'IT 운영',
    tags: ['보안', '컴플라이언스', '감사'],
    skillCount: 4,
    skills: [
      { command: 'sec_audit_run', desc: '보안 통제 항목 감사' },
      { command: 'sec_compliance_check', desc: 'ISO 27001·SOC 2 컴플라이언스 점검' },
      { command: 'sec_vuln_assess', desc: 'CVE 매핑·취약점 우선순위' },
      { command: 'sec_policy_enforce', desc: '정책 위반 탐지·조치 가이드' },
    ],
    gains: '보안 감사 · 컴플라이언스 점검 · 취약점 평가 · 정책 시행',
    useCases: ['보안 감사', '컴플라이언스 검토', '리스크 평가'],
    connectors: [
      { name: 'QRadar SIEM', desc: '보안 이벤트·로그 조회' },
      { name: '취약점 스캐너', desc: '자산 스캔 결과 수집' },
      { name: '정책 저장소', desc: '통제·기준 문서 접근' },
    ],
    knowledge: [
      { name: '보안 통제 카탈로그', desc: '통제 항목·구현 가이드', meta: '230 문서' },
      { name: '컴플라이언스 체크리스트', desc: 'ISO 27001 · SOC 2 점검표', meta: '갱신됨' },
      { name: '취약점 지식 베이스', desc: 'CVE·완화 조치 매핑', meta: '실시간' },
    ],
    installed: false,
    enabled: false,
    icon: 'security',
  },
  {
    id: 'plugin-9',
    name: '고객 성공 전문가',
    description: '선제적 참여와 분석으로 고객 성공을 이끌어냅니다',
    version: '1.4.0',
    author: '고객 성공 팀',
    provider: 'IBM Customer Success',
    category: '영업',
    tags: ['고객성공', '참여', '유지'],
    skillCount: 4,
    skills: [
      { command: 'cs_health_score', desc: '계정 건강도 산정' },
      { command: 'cs_engagement_track', desc: '사용 로그 참여도 분석' },
      { command: 'cs_renewal_predict', desc: '갱신·이탈 신호 예측' },
      { command: 'cs_success_plan', desc: '온보딩 성공 마일스톤 설계' },
    ],
    gains: '건강도 스코어링 · 참여도 추적 · 갱신 예측 · 성공 계획',
    useCases: ['계정 관리', '갱신 계획', '확장'],
    connectors: [
      { name: 'Gainsight API', desc: '고객 건강도·플레이북 연동' },
      { name: '제품 사용 로그', desc: '기능별 사용량 데이터' },
      { name: 'Zendesk', desc: '지원 티켓·만족도 조회' },
    ],
    knowledge: [
      { name: '고객 건강도 모델', desc: '점수 산정 지표·임계값', meta: 'v1.4' },
      { name: '갱신 예측 지표', desc: '이탈 신호·선행 지표 정의', meta: '갱신됨' },
      { name: '온보딩 플레이북', desc: '단계별 성공 마일스톤', meta: '54 문서' },
    ],
    installed: false,
    enabled: false,
    icon: 'user-favorite',
  },
  {
    id: 'plugin-10',
    name: '데이터 사이언스 전문가',
    description: 'MLOps 모범 사례로 머신러닝 모델을 구축하고 배포합니다',
    version: '2.5.0',
    author: '데이터 사이언스 팀',
    provider: 'IBM Data Science',
    category: 'WXO',
    tags: ['ml', '데이터사이언스', 'mlops'],
    skillCount: 5,
    skills: [
      { command: 'ds_model_develop', desc: '베이스라인 모델 학습' },
      { command: 'ds_feature_engineer', desc: '피처 생성·선택' },
      { command: 'ds_model_deploy', desc: '서빙·롤아웃 배포 구성' },
      { command: 'ds_drift_monitor', desc: '성능·데이터 드리프트 탐지' },
      { command: 'ds_pipeline_automate', desc: 'MLOps 파이프라인 구성' },
    ],
    gains: '모델 개발 · 피처 엔지니어링 · 모델 배포 · 성능 모니터링',
    useCases: ['모델 구축', '프로덕션 배포', '모니터링'],
    connectors: [
      { name: 'watsonx.ai', desc: '모델 학습·추론 실행' },
      { name: '모델 레지스트리', desc: '버전·메타데이터 관리' },
      { name: '피처 스토어', desc: '피처 조회·재사용' },
    ],
    knowledge: [
      { name: 'MLOps 베스트 프랙티스', desc: '파이프라인·재현성 가이드', meta: '180 문서' },
      { name: '모델 배포 가이드', desc: '서빙·롤아웃·롤백 전략', meta: 'v2.5' },
      { name: '모니터링·드리프트 문서', desc: '성능·데이터 드리프트 탐지', meta: '갱신됨' },
    ],
    installed: false,
    enabled: false,
    icon: 'chart-network',
  },
];

// Made with Bob
