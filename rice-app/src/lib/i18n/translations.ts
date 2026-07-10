export const translations = {
  en: {
    // Settings Screen
    settings: {
      title: 'Settings',
      saved: 'Saved',
      saveButton: 'Save Settings',
      
      // Appearance Section
      appearance: {
        title: 'Appearance',
        description: 'Theme, language, and font preferences',
        theme: 'Theme',
        themeDark: 'Dark',
        themeLight: 'Light',
        language: 'Language',
        font: 'Font',
        fontSystem: 'System default',
        fontMono: 'Monospace',
        fontSans: 'Sans-serif',
      },
      
      // API & Runtime Section
      apiRuntime: {
        title: 'API & Runtime',
        description: 'Model provider credentials and local runtime',
        watsonxApiKey: 'Claude API key',
        watsonxPlaceholder: 'Enter your Anthropic (Claude) API key',
        watsonxHelper: 'Stored locally on this device. Required for the Claude (SaaS) model.',
        ollamaEndpoint: 'Ollama endpoint',
        ollamaPlaceholder: 'http://localhost:11434',
        runtimeAutoStart: 'Start runtime on launch',
        runtimeAutoStartDesc: 'Automatically start the RICE runtime when the app opens',
        show: 'Show',
        hide: 'Hide',
      },
      
      // Learning & Privacy Section
      learningPrivacy: {
        title: 'Learning · Privacy',
        description: 'Control what the learning loop can collect and where it lives',
        userModeling: 'Enable user modeling',
        userModelingDesc: 'Learn from your interactions to personalize responses',
        localStorage: 'Store user model locally only',
        localStorageDesc: 'Keep all learning data on your device',
        telemetry: 'Share anonymous usage data',
        telemetryDesc: 'Help improve RICE by sending anonymized telemetry',
      },
      
      // About Section
      about: {
        title: 'About',
        appName: 'RICE',
        appDescription: 'Runtime Intelligence & Continuous Evolution · v0.1.0',
        beta: 'Beta',
      },

      // Danger Zone Section
      danger: {
        title: 'Danger zone',
        description: 'Irreversible actions — be careful',
        reset: 'Reset all settings',
        resetDesc: 'Restore every setting to its default value',
        resetButton: 'Reset',
      },
    },
    
    // Chat Screen
    chat: {
      title: 'Chat',
      placeholder: 'Type a message...',
      send: 'Send',
    },
    
    // Skill Market Screen
    skillMarket: {
      title: 'Skill Market',
      search: 'Search skills...',
      install: 'Install',
      installed: 'Installed',
      uninstall: 'Uninstall',
    },
    
    // Plugin Market Screen
    pluginMarket: {
      title: 'Plugin Market',
      search: 'Search plugins...',
      install: 'Install',
      installed: 'Installed',
      uninstall: 'Uninstall',
    },
    
    // Common
    common: {
      loading: 'Loading...',
      error: 'Error',
      success: 'Success',
      cancel: 'Cancel',
      confirm: 'Confirm',
      close: 'Close',
    },
  },
  
  ko: {
    // Settings Screen
    settings: {
      title: '설정',
      saved: '저장됨',
      saveButton: '설정 저장',
      
      // Appearance Section
      appearance: {
        title: '외관',
        description: '테마, 언어, 글꼴 설정',
        theme: '테마',
        themeDark: '다크',
        themeLight: '라이트',
        language: '언어',
        font: '글꼴',
        fontSystem: '시스템 기본',
        fontMono: '고정폭',
        fontSans: '산세리프',
      },
      
      // API & Runtime Section
      apiRuntime: {
        title: 'API 및 런타임',
        description: '모델 제공자 자격 증명 및 로컬 런타임',
        watsonxApiKey: 'Claude API 키',
        watsonxPlaceholder: 'Anthropic(Claude) API 키를 입력하세요',
        watsonxHelper: '이 기기에 로컬로 저장됩니다. Claude(SaaS) 모델에 필요합니다.',
        ollamaEndpoint: 'Ollama 엔드포인트',
        ollamaPlaceholder: 'http://localhost:11434',
        runtimeAutoStart: '시작 시 런타임 자동 실행',
        runtimeAutoStartDesc: '앱이 열릴 때 RICE 런타임을 자동으로 시작합니다',
        show: '보기',
        hide: '숨기기',
      },
      
      // Learning & Privacy Section
      learningPrivacy: {
        title: '학습 · 개인정보',
        description: '학습 루프가 수집할 수 있는 항목과 저장 위치를 제어합니다',
        userModeling: '사용자 모델링 활성화',
        userModelingDesc: '상호작용을 통해 학습하여 응답을 개인화합니다',
        localStorage: '사용자 모델을 로컬에만 저장',
        localStorageDesc: '모든 학습 데이터를 기기에 보관합니다',
        telemetry: '익명 사용 데이터 공유',
        telemetryDesc: '익명화된 원격 측정을 전송하여 RICE 개선에 도움을 줍니다',
      },
      
      // About Section
      about: {
        title: '정보',
        appName: 'RICE',
        appDescription: '런타임 인텔리전스 및 지속적 진화 · v0.1.0',
        beta: '베타',
      },

      // Danger Zone Section
      danger: {
        title: '위험 구역',
        description: '되돌릴 수 없는 작업 — 주의하세요',
        reset: '모든 설정 초기화',
        resetDesc: '모든 설정을 기본값으로 되돌립니다',
        resetButton: '초기화',
      },
    },
    
    // Chat Screen
    chat: {
      title: '채팅',
      placeholder: '메시지를 입력하세요...',
      send: '전송',
    },
    
    // Skill Market Screen
    skillMarket: {
      title: '스킬 마켓',
      search: '스킬 검색...',
      install: '설치',
      installed: '설치됨',
      uninstall: '제거',
    },
    
    // Plugin Market Screen
    pluginMarket: {
      title: '플러그인 마켓',
      search: '플러그인 검색...',
      install: '설치',
      installed: '설치됨',
      uninstall: '제거',
    },
    
    // Common
    common: {
      loading: '로딩 중...',
      error: '오류',
      success: '성공',
      cancel: '취소',
      confirm: '확인',
      close: '닫기',
    },
  },
} as const;

export type Language = keyof typeof translations;
export type TranslationKeys = typeof translations.en;

// Made with Bob
