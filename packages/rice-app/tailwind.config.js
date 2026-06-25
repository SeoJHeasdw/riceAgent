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
          '01': 'var(--cds-layer-01)',
          '02': 'var(--cds-layer-02)',
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
        // Carbon palette shades used directly in components
        blue: {
          '10': '#edf5ff',
          '30': '#a6c8ff',
          '50': '#4589ff',
          '60': '#0f62fe',
          '70': '#0043ce',
        },
        red: {
          '60': '#da1e28',
          '70': '#ba1b23',
        },
        purple: {
          '60': '#8a3ffc',
        },
        teal: {
          '60': '#009d9a',
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
            '01': 'var(--cds-border-strong-01)',
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
          helper: 'var(--cds-text-helper)',
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
          error: 'var(--cds-support-error)',
          success: 'var(--cds-support-success)',
          warning: 'var(--cds-support-warning)',
          info: 'var(--cds-support-info)',
        },
        interactive: 'var(--cds-interactive)',
        focus: 'var(--cds-focus)',
        'focus-inset': 'var(--cds-focus-inset)',
        'focus-inverse': 'var(--cds-focus-inverse)',
      },
      fontFamily: {
        sans: ['IBM Plex Sans', 'system-ui', '-apple-system', 'sans-serif'],
        mono: ['IBM Plex Mono', 'Menlo', 'Monaco', 'Courier New', 'monospace'],
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
        // Elevation scale tuned for dark surfaces:
        // soft ambient shadow + 1px top inner highlight for edge definition
        'elevation-1': '0 1px 2px rgba(0, 0, 0, 0.32), 0 1px 4px rgba(0, 0, 0, 0.18), inset 0 1px 0 rgba(255, 255, 255, 0.04)',
        'elevation-2': '0 2px 4px rgba(0, 0, 0, 0.32), 0 4px 12px rgba(0, 0, 0, 0.24), inset 0 1px 0 rgba(255, 255, 255, 0.05)',
        'elevation-3': '0 4px 8px rgba(0, 0, 0, 0.36), 0 8px 24px rgba(0, 0, 0, 0.28), inset 0 1px 0 rgba(255, 255, 255, 0.06)',
        'overlay': '0 12px 32px rgba(0, 0, 0, 0.45), 0 24px 64px rgba(0, 0, 0, 0.35), inset 0 1px 0 rgba(255, 255, 255, 0.07)',
        'glow-interactive': '0 0 0 1px var(--cds-interactive), 0 4px 16px rgba(15, 98, 254, 0.35)',
      },
      fontSize: {
        // IBM Carbon type scale
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
