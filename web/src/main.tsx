// Author: @SaadZarook
import "@fontsource/ibm-plex-sans/400.css";
import "@fontsource/ibm-plex-sans/500.css";
import "@fontsource/ibm-plex-sans/600.css";
import "@fontsource/ibm-plex-mono/400.css";
import "./styles/tokens.css";
import "./styles/app.css";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import { App } from "./App";

const root = document.getElementById("root");
if (!root) {
  throw new Error("Missing #root element in index.html");
}

const queryClient = new QueryClient();

/** In mock mode the API is answered by MSW, shaped like the contract. */
async function enableMocking() {
  // Dot access, so Vite replaces it at build time and a release build drops the mocks entirely.
  if (import.meta.env.VITE_MOCK_API !== "true") return;
  const { worker } = await import("../mocks/browser");
  await worker.start({ quiet: true });
}

void enableMocking().then(() => {
  createRoot(root).render(
    <StrictMode>
      <QueryClientProvider client={queryClient}>
        <BrowserRouter>
          <App />
        </BrowserRouter>
      </QueryClientProvider>
    </StrictMode>,
  );
});
