/** @type {import('next').NextConfig} */
const nextConfig = {
  output: "standalone",
  // SC-0: enable the instrumentation register() hook (Next 14) so instrumentation.ts runs the
  // production-secret guard once at server startup. Stable (no flag) in Next 15.
  experimental: {
    instrumentationHook: true,
  },
};
export default nextConfig;
