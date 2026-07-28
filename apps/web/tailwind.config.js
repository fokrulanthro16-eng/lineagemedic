/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        // Severity palette. Named by meaning rather than by hue so the mapping
        // stays honest if the colours are ever retuned for contrast.
        critical: '#dc2626',
        warning: '#d97706',
        healthy: '#059669',
        cleared: '#64748b',
      },
    },
  },
  plugins: [],
}
