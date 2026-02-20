import type { Config } from "tailwindcss";

const config: Config = {
  content: [
    "./app/**/*.{js,ts,jsx,tsx}",
    "./components/**/*.{js,ts,jsx,tsx}",
    "./lib/**/*.{js,ts,jsx,tsx}"
  ],
  theme: {
    extend: {
      colors: {
        ink: "#0B132B",
        steel: "#1C2541",
        cyan: "#5BC0BE",
        mint: "#C4F1F9",
        sand: "#F2F2F2",
        ember: "#D95D39"
      },
      boxShadow: {
        panel: "0 10px 30px rgba(11, 19, 43, 0.12)"
      }
    }
  },
  plugins: []
};

export default config;
