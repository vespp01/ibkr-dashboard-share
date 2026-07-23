import tseslint from 'typescript-eslint';
import reactHooks from 'eslint-plugin-react-hooks';

export default tseslint.config(
  { ignores: ['dist/'] },
  {
    files: ['src/**/*.{ts,tsx}'],
    extends: [tseslint.configs.recommended, reactHooks.configs.flat.recommended],
    rules: {
      // Deuda preexistente (40 usos de `any` en src/ a 2026-06-10): warn, no error.
      // Endurecer a 'error' cuando se tipen las respuestas de la API.
      '@typescript-eslint/no-explicit-any': 'warn',
    },
  },
);
