/** @type {import('tailwindcss').Config} */
export default {
  content: [
    "./index.html",
    "./src/**/*.{js,ts,jsx,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        // IBM Carbon color tokens
        background: 'var(--cds-background)',
        layer: {
          DEFAULT: 'var(--cds-layer)',
          // /opacity 수식이 실제 동작하도록 alpha 주입 가능한 형태로 (자주 쓰는 층만)
          '01': 'rgb(var(--cds-layer-01-rgb) / <alpha-value>)',
          '02': 'rgb(var(--cds-layer-02-rgb) / <alpha-value>)',
          '03': 'var(--cds-layer-03)',
          accent: {
            DEFAULT: 'var(--cds-layer-accent)',
            '01': 'var(--cds-layer-accent-01)',
            '02': 'var(--cds-layer-accent-02)',
            '03': 'var(--cds-layer-accent-03)',
          },
          hover: {
            '01': 'var(--cds-layer-hover-01)',
            '02': 'var(--cds-layer-hover-02)',
          },
          active: {
            '01': 'var(--cds-layer-active-01)',
          },
          selected: {
            '01': 'var(--cds-layer-selected-01)',
          },
        },
        // Carbon palette shades used directly in components.
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
        'interactive-hover': 'var(--cds-interactive-hover)',
        'interactive-active': 'var(--cds-interactive-active)',
        'danger-hover': 'var(--cds-danger-hover)',
        'danger-active': 'var(--cds-danger-active)',
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
        overlay: 'var(--cds-overlay)',
        field: {
          DEFAULT: 'var(--cds-field)',
          '01': 'var(--cds-field-01)',
          '02': 'var(--cds-field-02)',
          '03': 'var(--cds-field-03)',
        },
        border: {
          subtle: {
            DEFAULT: 'var(--cds-border-subtle)',
            '00': 'var(--cds-border-subtle-00)',
            '01': 'var(--cds-border-subtle-01)',
            '02': 'var(--cds-border-subtle-02)',
            '03': 'var(--cds-border-subtle-03)',
          },
          strong: {
            DEFAULT: 'var(--cds-border-strong)',
            '01': 'rgb(var(--cds-border-strong-01-rgb) / <alpha-value>)',
            '02': 'var(--cds-border-strong-02)',
            '03': 'var(--cds-border-strong-03)',
          },
          inverse: 'var(--cds-border-inverse)',
          interactive: 'var(--cds-border-interactive)',
        },
        text: {
          primary: 'var(--cds-text-primary)',
          secondary: 'var(--cds-text-secondary)',
          placeholder: 'var(--cds-text-placeholder)',
          helper: 'rgb(var(--cds-text-helper-rgb) / <alpha-value>)',
          error: 'var(--cds-text-error)',
          inverse: 'var(--cds-text-inverse)',
          'on-color': 'var(--cds-text-on-color)',
          'on-color-disabled': 'var(--cds-text-on-color-disabled)',
        },
        link: {
          primary: 'var(--cds-link-primary)',
          secondary: 'var(--cds-link-secondary)',
          visited: 'var(--cds-link-visited)',
          inverse: 'var(--cds-link-inverse)',
        },
        icon: {
          primary: 'var(--cds-icon-primary)',
          secondary: 'var(--cds-icon-secondary)',
          inverse: 'var(--cds-icon-inverse)',
          'on-color': 'var(--cds-icon-on-color)',
          'on-color-disabled': 'var(--cds-icon-on-color-disabled)',
        },
        support: {
          error: 'rgb(var(--cds-support-error-rgb) / <alpha-value>)',
          success: 'rgb(var(--cds-support-success-rgb) / <alpha-value>)',
          warning: 'rgb(var(--cds-support-warning-rgb) / <alpha-value>)',
          info: 'rgb(var(--cds-support-info-rgb) / <alpha-value>)',
        },
        interactive: 'rgb(var(--cds-interactive-rgb) / <alpha-value>)',
        focus: 'var(--cds-focus)',
        'focus-inset': 'var(--cds-focus-inset)',
        'focus-inverse': 'var(--cds-focus-inverse)',
      },
      fontFamily: {
        sans: ['var(--font-sans)'],
        mono: ['var(--font-mono)'],
        serif: ['IBM Plex Serif', 'Georgia', 'Times New Roman', 'serif'],
      },
      spacing: {
        // IBM Carbon 8px spacing scale
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
        // Modernized radius scale (departs from Carbon's flat 0px)
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
        // IBM Carbon type scale
        // label-micro: Carbon 스케일(최소 12px) 아래 유일한 공식 마이크로 단계.
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
        // IBM Carbon motion tokens
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

// Made with Bob
