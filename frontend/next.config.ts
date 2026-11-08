import path from "node:path";
import { fileURLToPath } from "node:url";
import { loadEnvConfig } from "@next/env";
import type { NextConfig } from "next";

const projectRoot = path.dirname(fileURLToPath(import.meta.url));
// Load BusinessSense/.env (parent of frontend/) so Supabase + extract URL live in one file.
loadEnvConfig(path.join(projectRoot, ".."));

const nextConfig: NextConfig = {
  turbopack: {
    root: projectRoot,
    rules: {
      "*.css": {
        loaders: ["@tailwindcss/turbopack"],
        as: "*.css",
      },
    },
  },
};

export default nextConfig;
