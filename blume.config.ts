import { defineConfig } from "blume";

export default defineConfig({
  title: "FDL",
  description: "Versions of a DuckLake catalog on object storage, kept the way Git keeps commits",
  theme: {
    accent: "#673ab7",
  },
  deployment: {
    site: "https://fdl.flo8s.com",
  },
  content: {
    root: "docs",
  },
  redirects: [{ from: "/cli", to: "/reference/cli" }],
});
