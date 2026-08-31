import type { Config } from "tailwindcss";
import animate from "tailwindcss-animate";

// Design tokens — see ADR-0037 § Visual identity.
// All colours are declared via CSS variables in src/index.css so a
// runtime theme switch flips them in one place.

export default {
  darkMode: ["class", "[data-theme='dark']"],
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    container: {
      center: true,
      padding: "1.5rem",
      screens: {
        "2xl": "1280px",
      },
    },
    extend: {
      colors: {
        background: "hsl(var(--background) / <alpha-value>)",
        foreground: "hsl(var(--foreground) / <alpha-value>)",
        muted: {
          DEFAULT: "hsl(var(--muted) / <alpha-value>)",
          foreground: "hsl(var(--muted-foreground) / <alpha-value>)",
        },
        card: {
          DEFAULT: "hsl(var(--card) / <alpha-value>)",
          foreground: "hsl(var(--card-foreground) / <alpha-value>)",
        },
        border: "hsl(var(--border) / <alpha-value>)",
        input: "hsl(var(--input) / <alpha-value>)",
        ring: "hsl(var(--ring) / <alpha-value>)",
        primary: {
          DEFAULT: "hsl(var(--primary) / <alpha-value>)",
          foreground: "hsl(var(--primary-foreground) / <alpha-value>)",
        },
        secondary: {
          DEFAULT: "hsl(var(--secondary) / <alpha-value>)",
          foreground: "hsl(var(--secondary-foreground) / <alpha-value>)",
        },
        accent: {
          DEFAULT: "hsl(var(--accent) / <alpha-value>)",
          foreground: "hsl(var(--accent-foreground) / <alpha-value>)",
        },
        destructive: {
          DEFAULT: "hsl(var(--destructive) / <alpha-value>)",
          foreground: "hsl(var(--destructive-foreground) / <alpha-value>)",
        },
        "voice-accent": "hsl(var(--voice-accent) / <alpha-value>)",
        brass: {
          DEFAULT: "hsl(38 52% 53%)",
          deep: "hsl(38 46% 42%)",
          soft: "hsl(38 70% 75%)",
        },
        navy: {
          DEFAULT: "hsl(222 38% 9%)",
          deep: "hsl(222 44% 6%)",
        },
        bone: {
          DEFAULT: "hsl(36 38% 96%)",
          warm: "hsl(36 26% 90%)",
        },
      },
      fontFamily: {
        // "… Variable" families are the self-hosted @fontsource woff2 (bundled
        // locally — no CDN egress). "Inter"/"Fraunces" remain as fallbacks for
        // any host that has the static font installed; then the system stack.
        sans: ["Inter Variable", "Inter", "system-ui", "-apple-system", "sans-serif"],
        serif: ["Fraunces Variable", "Fraunces", "Georgia", "serif"],
        mono: ["JetBrains Mono", "ui-monospace", "monospace"],
      },
      borderRadius: {
        lg: "var(--radius)",
        md: "calc(var(--radius) - 2px)",
        sm: "calc(var(--radius) - 4px)",
      },
      keyframes: {
        "fade-in": {
          from: { opacity: "0", transform: "translateY(4px)" },
          to: { opacity: "1", transform: "translateY(0)" },
        },
        shimmer: {
          "100%": { transform: "translateX(100%)" },
        },
      },
      animation: {
        "fade-in": "fade-in 240ms ease-out",
        shimmer: "shimmer 2.2s ease-in-out infinite",
      },
    },
  },
  plugins: [animate],
} satisfies Config;
