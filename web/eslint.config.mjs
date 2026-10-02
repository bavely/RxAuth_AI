import coreWebVitals from "eslint-config-next/core-web-vitals";
import typescript from "eslint-config-next/typescript";

/**
 * `eslint-config-next` ships flat config directly, so there is no `FlatCompat`
 * shim and no `@eslint/eslintrc` dependency to keep in step with ESLint itself.
 *
 * ESLint stays on 9.x: `eslint-config-next` 16 bundles an `eslint-plugin-react`
 * that calls a context API removed in ESLint 10, so 10 crashes before it lints
 * anything. Pinned to what the framework's own config is tested against rather
 * than to the newest number.
 */
const config = [
  { ignores: [".next/**", "node_modules/**", "next-env.d.ts"] },
  ...coreWebVitals,
  ...typescript,
  {
    rules: {
      // A server action's signature is fixed by `useActionState`: the previous
      // state arrives whether or not the action reads it. Underscore marks the
      // ones deliberately ignored, rather than deleting a parameter the
      // framework will pass anyway.
      "@typescript-eslint/no-unused-vars": [
        "error",
        { argsIgnorePattern: "^_", varsIgnorePattern: "^_", caughtErrorsIgnorePattern: "^_" },
      ],
    },
  },
];

export default config;
