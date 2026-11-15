// Author: @SaadZarook

/** Put text on the clipboard; false if the browser would not, so the screen can say so. */
export async function copyText(text: string): Promise<boolean> {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    return false;
  }
}
