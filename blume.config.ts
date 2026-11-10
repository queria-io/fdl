import { defineConfig } from "blume";

export default defineConfig({
  title: "FDL",
  description: "Versions of a DuckLake catalog on object storage, kept the way Git keeps commits",
  theme: {
    accent: "#673ab7",
  },
  deployment: {
    site: "https://fdl.queria.io",
  },
  content: {
    root: "docs",
  },
  i18n: {
    defaultLocale: "en",
    locales: [
      { code: "en", label: "English" },
      { code: "ja", label: "日本語" },
    ],
  },
  seo: {
    og: {
      fonts: ["Noto Sans JP"],
    },
  },
  redirects: [{ from: "/cli", to: "/reference/cli" }],
});
