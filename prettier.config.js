/** @type {import("prettier").Config} */
export default {
  semi: true,
  singleQuote: false,
  trailingComma: "es5",
  tabWidth: 2,
  useTabs: false,
  printWidth: 100,
  bracketSpacing: true,
  arrowParens: "always",
  endOfLine: "lf",
  plugins: ["@trivago/prettier-plugin-sort-imports"],
  importOrder: ["^node:(.*)$", "<THIRD_PARTY_MODULES>", "^@profile-scorer/(.*)$", "^[./]"],
  importOrderSeparation: true,
  importOrderSortSpecifiers: true,
};
