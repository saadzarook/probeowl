// Author: @SaadZarook
import "@fontsource/ibm-plex-sans/400.css";
import "@fontsource/ibm-plex-sans/500.css";
import "@fontsource/ibm-plex-sans/600.css";
import "@fontsource/ibm-plex-mono/400.css";
import "./ui/ui.css";
import "./styles/tokens.css";
import "./styles/app.css";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { StrictMode, type ReactNode } from "react";
import { setNonce } from "get-nonce";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import { App } from "./App";
import { cspNonce } from "./csp";

// The Sheet's scroll lock (Radix, react-remove-scroll) adds a <style>: it carries the page's nonce.
if (cspNonce) setNonce(cspNonce);

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
  // For the e2e tests only (mock builds; a release build drops this with the mocks): what the
  // mutation cache still holds, so a test can prove no password outlives its form.
  (window as unknown as { probeowlMutations?: () => unknown[] }).probeowlMutations = () =>
    queryClient.getMutationCache().getAll().map((m) => m.state);
}

/** The UI components' gallery, for their rule tests (#130): in mock builds only, like the mocks. */
async function galleryPage(): Promise<ReactNode | undefined> {
  // Dot access, as above, so a release build drops the gallery with the mocks.
  if (import.meta.env.VITE_MOCK_API !== "true" || window.location.pathname !== "/ui-gallery") return undefined;
  const { UiGallery } = await import("../mocks/gallery/UiGallery");
  return <UiGallery />;
}

void enableMocking()
  .then(galleryPage)
  .then((gallery) => {
    createRoot(root).render(
      <StrictMode>
        {gallery ?? (
          <QueryClientProvider client={queryClient}>
            <BrowserRouter>
              <App />
            </BrowserRouter>
          </QueryClientProvider>
        )}
      </StrictMode>,
    );
  });
