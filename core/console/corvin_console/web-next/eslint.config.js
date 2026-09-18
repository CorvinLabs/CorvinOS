import js from '@eslint/js';
import tseslint from 'typescript-eslint';
import reactPlugin from 'eslint-plugin-react';
import reactHooksPlugin from 'eslint-plugin-react-hooks';

export default [
  // Build outputs: `dist/` is the live bundle, `dist.next/` / `dist.prev/` are the
  // swap + rollback copies scripts/console-deploy.sh keeps next to it. Linting them
  // reported ~900 minified-bundle "errors" and buried the real source problems.
  { ignores: ['dist', 'dist.next', 'dist.prev', 'node_modules', 'coverage', '.next', 'playwright-report', 'test-results'] },
  {
    files: ['**/*.{js,jsx,ts,tsx}'],
    languageOptions: {
      ecmaVersion: 2020,
      sourceType: 'module',
      globals: {
        console: 'readonly',
        document: 'readonly',
        window: 'readonly',
        navigator: 'readonly',
      },
      parserOptions: {
        ecmaFeatures: { jsx: true },
      },
    },
    rules: {
      ...js.configs.recommended.rules,
      'no-unused-vars': 'off',
      'no-undef': 'off',
    },
  },
  ...tseslint.configs.recommended.map(config => ({
    ...config,
    files: ['**/*.{ts,tsx}'],
    rules: {
      ...config.rules,
      '@typescript-eslint/no-unused-vars': [
        'error',
        {
          argsIgnorePattern: '^_',
          varsIgnorePattern: '^_',
          caughtErrorsIgnorePattern: '^_'
        },
      ],
      '@typescript-eslint/no-explicit-any': 'error',
    },
  })),
  {
    // ADR-0885: the Models console never calls fetch() itself — every request
    // goes through lib/api/client.ts::api(), which sets X-CSRF-Token on writes.
    // (The old cost panel's bare fetch() writes all answered 403 on the live host.)
    files: ['src/pages/models/**/*.{ts,tsx}'],
    rules: { 'no-restricted-globals': ['error', 'fetch'] },
  },
  {
    files: ['**/*.{jsx,tsx}'],
    plugins: {
      react: reactPlugin,
      'react-hooks': reactHooksPlugin,
    },
    rules: {
      'react-hooks/exhaustive-deps': 'warn',
      'react-hooks/rules-of-hooks': 'error',
    },
  },
];
