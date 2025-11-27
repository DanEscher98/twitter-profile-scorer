import eslint from "@eslint/js";
import prettier from "eslint-config-prettier";
import globals from "globals";
import tseslint from "typescript-eslint";

export default tseslint.config(
  eslint.configs.recommended,
  ...tseslint.configs.recommended,
  prettier,
  {
    ignores: [
      "**/node_modules/",
      "**/dist/",
      "**/.next/",
      "**/out/",
      ".yarn/",
      "infra/",
      "certs/",
      "**/*.min.js",
    ],
  },
  {
    rules: {
      // Allow unused variables that start with underscore
      "@typescript-eslint/no-unused-vars": [
        "error",
        {
          argsIgnorePattern: "^_",
          varsIgnorePattern: "^_",
          caughtErrorsIgnorePattern: "^_",
        },
      ],
      // Enforce semicolons (Prettier handles this, but good to have)
      semi: "off",
      "@typescript-eslint/semi": "off",
    },
  },
  // Node.js globals for config files and scripts
  {
    files: ["**/*.config.{js,mjs,ts}", "**/esbuild.config.mjs"],
    languageOptions: {
      globals: {
        ...globals.node,
      },
    },
  }
);
