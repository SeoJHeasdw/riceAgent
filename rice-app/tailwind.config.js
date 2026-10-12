/** @type {import('tailwindcss').Config} */
export default {
  content: [
    "./index.html",
    "./src/**/*.{js,ts,jsx,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        // RICE color tokens
        background: 'var(--rice-background)',
        layer: {
          DEFAULT: 'var(--rice-layer)',
          // /opacity 수식이 실제 동작하도록 alpha 주입 가능한 형태로 (자주 쓰는 층만)
          '01': 'rgb(var(--rice-layer-01-rgb) / <alpha-value>)',
          '02': 'rgb(var(--rice-layer-02-rgb) / <alpha-value>)',
          '03': 'var(--rice-layer-03)',
          accent: {
            DEFAULT: 'var(--rice-layer-accent)',
            '01': 'var(--rice-layer-accent-01)',
            '02': 'var(--rice-layer-accent-02)',
            '03': 'var(--rice-layer-accent-03)',
          },
          hover: {
            '01': 'var(--rice-layer-hover-01)',
            '02': 'var(--rice-layer-hover-02)',
          },
          active: {
            '01': 'var(--rice-layer-active-01)',
          },
          selected: {
            '01': 'var(--rice-layer-selected-01)',
          },
        },
        // RICE palette shades used directly in components.
        // 임의값 유틸(text-[#...])은 금지 — 여기 이름 붙은 단계만 쓴다.
        // Theme-aware palette aliases. Foreground steps become darker in the
        // light theme while translucent backgrounds keep the same hue family.
        blue: {
          '10': 'rgb(var(--rice-blue-10-rgb) / <alpha-value>)',
          '30': 'rgb(var(--rice-blue-30-rgb) / <alpha-value>)',
          '40': 'rgb(var(--rice-blue-40-rgb) / <alpha-value>)',
          '50': 'rgb(var(--rice-blue-50-rgb) / <alpha-value>)',
          '60': 'rgb(var(--rice-blue-60-rgb) / <alpha-value>)',
          '70': 'rgb(var(--rice-blue-70-rgb) / <alpha-value>)',
          '80': 'rgb(var(--rice-blue-80-rgb) / <alpha-value>)',
          '100': 'rgb(var(--rice-blue-100-rgb) / <alpha-value>)',
        },
        red: {
          '30': 'rgb(var(--rice-red-30-rgb) / <alpha-value>)',
          '40': 'rgb(var(--rice-red-40-rgb) / <alpha-value>)',
          '50': 'rgb(var(--rice-red-50-rgb) / <alpha-value>)',
          '60': 'rgb(var(--rice-red-60-rgb) / <alpha-value>)',
          '70': 'rgb(var(--rice-red-70-rgb) / <alpha-value>)',
          '80': 'rgb(var(--rice-red-80-rgb) / <alpha-value>)',
        },
        green: {
          '30': 'rgb(var(--rice-green-30-rgb) / <alpha-value>)',
          '40': 'rgb(var(--rice-green-40-rgb) / <alpha-value>)',
          '50': 'rgb(var(--rice-green-50-rgb) / <alpha-value>)',
          '80': 'rgb(var(--rice-green-80-rgb) / <alpha-value>)',
        },
        yellow: {
          '20': 'rgb(var(--rice-yellow-20-rgb) / <alpha-value>)',
          '30': 'rgb(var(--rice-yellow-30-rgb) / <alpha-value>)',
          '40': 'rgb(var(--rice-yellow-40-rgb) / <alpha-value>)',
          '50': 'rgb(var(--rice-yellow-50-rgb) / <alpha-value>)',
          '60': 'rgb(var(--rice-yellow-60-rgb) / <alpha-value>)',
          '70': 'rgb(var(--rice-yellow-70-rgb) / <alpha-value>)',
        },
        orange: {
          '30': 'rgb(var(--rice-orange-30-rgb) / <alpha-value>)',
          '40': 'rgb(var(--rice-orange-40-rgb) / <alpha-value>)',
        },
        magenta: {
          '50': 'rgb(var(--rice-magenta-50-rgb) / <alpha-value>)',
        },
        gray: {
          '70': 'rgb(var(--rice-gray-70-rgb) / <alpha-value>)',
        },
        purple: {
          '30': 'rgb(var(--rice-purple-30-rgb) / <alpha-value>)',
          '40': 'rgb(var(--rice-purple-40-rgb) / <alpha-value>)',
          '50': 'rgb(var(--rice-purple-50-rgb) / <alpha-value>)',
          '60': 'rgb(var(--rice-purple-60-rgb) / <alpha-value>)',
          '70': 'rgb(var(--rice-purple-70-rgb) / <alpha-value>)',
          '80': 'rgb(var(--rice-purple-80-rgb) / <alpha-value>)',
        },
        teal: {
          '30': 'rgb(var(--rice-teal-30-rgb) / <alpha-value>)',
          '40': 'rgb(var(--rice-teal-40-rgb) / <alpha-value>)',
          '60': 'rgb(var(--rice-teal-60-rgb) / <alpha-value>)',
          '70': 'rgb(var(--rice-teal-70-rgb) / <alpha-value>)',
          '80': 'rgb(var(--rice-teal-80-rgb) / <alpha-value>)',
        },
        // 상태(hover/active) 시맨틱 — globals.css 토큰과 동기
        'interactive-hover': 'var(--rice-interactive-hover)',
        'interactive-active': 'var(--rice-interactive-active)',
        'danger-hover': 'var(--rice-danger-hover)',
        'danger-active': 'var(--rice-danger-active)',
        // diff 표면 (추가/삭제 행)
        diff: {
          'add-bg': 'var(--diff-add-bg)',
          'add-text': 'var(--diff-add-text)',
          'add-accent': 'var(--diff-add-accent)',
          'remove-bg': 'var(--diff-remove-bg)',
          'remove-text': 'var(--diff-remove-text)',
          'remove-accent': 'var(--diff-remove-accent)',
        },
        // Brand shell (titlebar + icon rail) — globals.css :root 정의와 동기
        shell: {
          DEFAULT: 'var(--rice-shell)',
          border: 'var(--rice-shell-border)',
          text: 'var(--rice-shell-text)',
          muted: 'var(--rice-shell-muted)',
          active: 'var(--rice-shell-active)',
          hover: 'var(--rice-shell-hover)',
          divider: 'var(--rice-shell-divider)',
        },
        // Domain accents (globals.css :root 정의와 동기)
        accent: {
          skill: 'var(--accent-skill)',
          plugin: 'var(--accent-plugin)',
          connector: 'var(--accent-connector)',
        },
        overlay: 'var(--rice-overlay)',
        field: {
          DEFAULT: 'var(--rice-field)',
          '01': 'var(--rice-field-01)',
          '02': 'var(--rice-field-02)',
          '03': 'var(--rice-field-03)',
        },
        border: {
          subtle: {
            DEFAULT: 'var(--rice-border-subtle)',
            '00': 'var(--rice-border-subtle-00)',
            '01': 'var(--rice-border-subtle-01)',
            '02': 'var(--rice-border-subtle-02)',
            '03': 'var(--rice-border-subtle-03)',
          },
          strong: {
            DEFAULT: 'var(--rice-border-strong)',
            '01': 'rgb(var(--rice-border-strong-01-rgb) / <alpha-value>)',
            '02': 'var(--rice-border-strong-02)',
            '03': 'var(--rice-border-strong-03)',
          },
          inverse: 'var(--rice-border-inverse)',
          interactive: 'var(--rice-border-interactive)',
        },
        text: {
          primary: 'var(--rice-text-primary)',
          secondary: 'var(--rice-text-secondary)',
          placeholder: 'var(--rice-text-placeholder)',
          helper: 'rgb(var(--rice-text-helper-rgb) / <alpha-value>)',
          error: 'var(--rice-text-error)',
          inverse: 'var(--rice-text-inverse)',
          'on-color': 'var(--rice-text-on-color)',
          'on-color-disabled': 'var(--rice-text-on-color-disabled)',
        },
        link: {
          primary: 'var(--rice-link-primary)',
          secondary: 'var(--rice-link-secondary)',
          visited: 'var(--rice-link-visited)',
          inverse: 'var(--rice-link-inverse)',
        },
        icon: {
          primary: 'var(--rice-icon-primary)',
          secondary: 'var(--rice-icon-secondary)',
          inverse: 'var(--rice-icon-inverse)',
          'on-color': 'var(--rice-icon-on-color)',
          'on-color-disabled': 'var(--rice-icon-on-color-disabled)',
        },
        support: {
          error: 'rgb(var(--rice-support-error-rgb) / <alpha-value>)',
          success: 'rgb(var(--rice-support-success-rgb) / <alpha-value>)',
          warning: 'rgb(var(--rice-support-warning-rgb) / <alpha-value>)',
          info: 'rgb(var(--rice-support-info-rgb) / <alpha-value>)',
        },
        interactive: 'rgb(var(--rice-interactive-rgb) / <alpha-value>)',
        focus: 'var(--rice-focus)',
        'focus-inset': 'var(--rice-focus-inset)',
        'focus-inverse': 'var(--rice-focus-inverse)',
      },
      fontFamily: {
        sans: ['var(--font-sans)'],
        mono: ['var(--font-mono)'],
        serif: ['Georgia', 'Times New Roman', 'serif'],
      },
      spacing: {
        // RICE 8px spacing scale
        '0': '0px',
        '0.5': '2px',
        '1': '4px',
        '2': '8px',
        '3': '12px',
        '4': '16px',
        '4.5': '20px',
        '5': '24px',
        '6': '32px',
        '7': '40px',
        '8': '48px',
        '10': '64px',
        '12': '80px',
        '14': '96px',
        '20': '160px',
      },
      borderRadius: {
        // Application radius scale
        none: '0px',
        sm: '6px',
        DEFAULT: '8px',
        md: '8px',
        lg: '10px',
        xl: '12px',
        '2xl': '16px',
        '3xl': '20px',
        full: '9999px',
      },
      boxShadow: {
        // Theme-aware elevation scale: soft ambient shadow + a top inner
        // highlight for edge definition.
        // 규율: 평면 카드에는 그림자 없음(border가 형태) — 그림자는 떠 있는
        // 표면(popover=2, modal/drawer/toast=3)에만 쓴다.
        'elevation-1': 'var(--rice-elevation-1)',
        'elevation-2': 'var(--rice-elevation-2)',
        'elevation-3': 'var(--rice-elevation-3)',
      },
      fontSize: {
        // RICE type scale
        // label-micro: RICE 스케일(최소 12px) 아래 유일한 공식 마이크로 단계.
        // 배지·Kbd·그룹라벨 전용 — 이 외 9~11.5px 임의값 금지.
        'label-micro': ['0.6875rem', { lineHeight: '0.875rem', letterSpacing: '0.32px' }],
        'caption-01': ['0.75rem', { lineHeight: '1rem', letterSpacing: '0.32px' }],
        'caption-02': ['0.875rem', { lineHeight: '1.125rem', letterSpacing: '0.32px' }],
        'label-01': ['0.75rem', { lineHeight: '1rem', letterSpacing: '0.32px' }],
        'label-02': ['0.875rem', { lineHeight: '1.125rem', letterSpacing: '0.16px' }],
        'helper-text-01': ['0.75rem', { lineHeight: '1rem', letterSpacing: '0.32px' }],
        'helper-text-02': ['0.875rem', { lineHeight: '1.125rem', letterSpacing: '0.16px' }],
        'body-short-01': ['0.875rem', { lineHeight: '1.125rem', letterSpacing: '0.16px' }],
        'body-short-02': ['1rem', { lineHeight: '1.375rem', letterSpacing: '0' }],
        'body-long-01': ['0.875rem', { lineHeight: '1.25rem', letterSpacing: '0.16px' }],
        'body-long-02': ['1rem', { lineHeight: '1.5rem', letterSpacing: '0' }],
        'code-01': ['0.75rem', { lineHeight: '1rem', letterSpacing: '0.32px' }],
        'code-02': ['0.875rem', { lineHeight: '1.25rem', letterSpacing: '0.32px' }],
        'heading-01': ['0.875rem', { lineHeight: '1.125rem', letterSpacing: '0.16px' }],
        'heading-02': ['1rem', { lineHeight: '1.375rem', letterSpacing: '0' }],
        'heading-03': ['1.25rem', { lineHeight: '1.625rem', letterSpacing: '0' }],
        'heading-04': ['1.75rem', { lineHeight: '2.25rem', letterSpacing: '0' }],
        'heading-05': ['2rem', { lineHeight: '2.5rem', letterSpacing: '0' }],
        'heading-06': ['2.625rem', { lineHeight: '3.125rem', letterSpacing: '0' }],
        'heading-07': ['3.375rem', { lineHeight: '4rem', letterSpacing: '0' }],
      },
      animation: {
        // RICE motion tokens
        'fade-in': 'fadeIn 0.11s cubic-bezier(0, 0, 0.38, 0.9)',
        'fade-out': 'fadeOut 0.11s cubic-bezier(0.2, 0, 1, 0.9)',
        'slide-in': 'slideIn 0.11s cubic-bezier(0, 0, 0.38, 0.9)',
        'slide-out': 'slideOut 0.11s cubic-bezier(0.2, 0, 1, 0.9)',
        'scale-in': 'scaleIn 0.18s cubic-bezier(0.16, 1, 0.3, 1)',
      },
      keyframes: {
        fadeIn: {
          '0%': { opacity: '0' },
          '100%': { opacity: '1' },
        },
        fadeOut: {
          '0%': { opacity: '1' },
          '100%': { opacity: '0' },
        },
        slideIn: {
          '0%': { transform: 'translateY(-8px)', opacity: '0' },
          '100%': { transform: 'translateY(0)', opacity: '1' },
        },
        slideOut: {
          '0%': { transform: 'translateY(0)', opacity: '1' },
          '100%': { transform: 'translateY(-8px)', opacity: '0' },
        },
        scaleIn: {
          '0%': { transform: 'scale(0.96)', opacity: '0' },
          '100%': { transform: 'scale(1)', opacity: '1' },
        },
      },
    },
  },
  plugins: [],
}
