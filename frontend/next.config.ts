import path from "node:path";
import { fileURLToPath } from "node:url";
import type { NextConfig } from "next";

const projectRoot = path.dirname(fileURLToPath(import.meta.url));

// Supabase / extract URL come from BusinessSense/.env via a hard-link at frontend/.env
// (one physical file; Next loads frontend/.env natively for NEXT_PUBLIC_*).

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
