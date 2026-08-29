const path = require("path");

/** @type {import('next').NextConfig} */
const nextConfig = {
    typescript: {
        ignoreBuildErrors: true,
    },
    reactStrictMode: true,
    allowedDevOrigins: ["127.0.0.1", "localhost"],
    turbopack: {
        root: path.join(__dirname),
    },
    env: {
        NEXT_PUBLIC_API_URL: process.env.NEXT_PUBLIC_API_URL || 'http://127.0.0.1:8000'
    },
}

module.exports = nextConfig
