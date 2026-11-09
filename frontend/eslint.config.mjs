// Code-style rules for the frontend: Next.js core-web-vitals and TypeScript recommendations.
import nextVitals from "eslint-config-next/core-web-vitals";
import nextTs from "eslint-config-next/typescript";

const config = [...nextVitals, ...nextTs];

export default config;
