// Author: @SaadZarook

/**
 * This page's style nonce, which owl serve writes into the page and names in its policy (#110).
 * CodeMirror and the Sheet's scroll lock add a <style>; only one carrying it is applied. Vite's
 * own servers send no policy, so there the placeholder stays and nothing checks it.
 */
export const cspNonce: string | undefined =
  document.querySelector<HTMLMetaElement>('meta[property="csp-nonce"]')?.nonce || undefined;
