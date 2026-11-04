import { defineConfig, globalIgnores } from "eslint/config";
import nextVitals from "eslint-config-next/core-web-vitals";
import nextTs from "eslint-config-next/typescript";

const eslintConfig = defineConfig([
  ...nextVitals,
  ...nextTs,
  // Override default ignores of eslint-config-next.
  globalIgnores([
    // Default ignores of eslint-config-next:
    ".next/**",
    "out/**",
    "build/**",
    "next-env.d.ts",
    // Generated from the API's OpenAPI schema by `make api-types`.
    "src/lib/api/schema.d.ts",
  ]),
  {
    // API shapes come from the generated schema only, so the UI cannot drift from the API.
    files: ["src/lib/api/**/*.ts"],
    rules: {
      "no-restricted-syntax": [
        "error",
        {
          selector: "TSTypeAliasDeclaration > TSTypeLiteral",
          message: "Use a type from schema.d.ts (make api-types) instead of declaring one.",
        },
        {
          selector: "TSInterfaceDeclaration",
          message: "Use a type from schema.d.ts (make api-types) instead of declaring one.",
        },
      ],
    },
  },
]);

export default eslintConfig;
