const configs = {
  chatgpt: {
    url: 'https://chatgpt.com/',
    input: ['textarea', '[contenteditable="true"]', '[data-testid="composer"]'],
    send: ['[data-testid="send-button"]', 'button[aria-label*="Send"]'],
    response: ['[data-message-author-role="assistant"]', '.markdown'],
  },
  claude: {
    url: 'https://claude.ai/',
    input: ['div[contenteditable="true"]', 'textarea'],
    send: ['button[aria-label*="Send"]', 'button[type="submit"]'],
    response: ['[data-testid="message"]', '.font-claude-message'],
  },
  gemini: {
    url: 'https://gemini.google.com/',
    input: ['rich-textarea textarea', 'div[contenteditable="true"]', 'textarea'],
    send: ['button[aria-label*="Send"]', 'button[aria-label*="send"]'],
    response: ['model-response', '.model-response-text'],
  },
  minimax: {
    url: 'https://hailuoai.com/',
    input: ['textarea', '[contenteditable="true"]', '.chat-input textarea', '.input-box textarea'],
    send: ['button[aria-label*="Send"]', 'button[type="submit"]', '.send-button', '.submit-btn'],
    response: ['.message-content', '.chat-message .content', '.response-text', '.markdown'],
  },
};

export function makeAiProviderScenario(provider) {
  return async function aiProviderSend({ page, params }) {
    const cfg = configs[provider];
    const text = params?.text || params?.prompt || '';
    if (!text) throw new Error('text required');
    if (!page.url().includes(new URL(cfg.url).hostname)) {
      await page.goto(cfg.url, { waitUntil: 'domcontentloaded', timeout: 60_000 });
    }
    const input = await firstVisible(page, cfg.input, 45_000);
    await input.click();
    await input.fill(text).catch(async () => {
      await page.keyboard.insertText(text);
    });
    const send = await firstVisible(page, cfg.send, 10_000).catch(() => null);
    if (send) await send.click();
    else await page.keyboard.press('Enter');
    await page.waitForTimeout(1000);
    const answer = await readLastText(page, cfg.response);
    return { sent: true, provider, text: answer };
  };
}

async function firstVisible(page, selectors, timeout) {
  const deadline = Date.now() + timeout;
  let lastError = null;
  while (Date.now() < deadline) {
    for (const selector of selectors) {
      try {
        const locator = page.locator(selector).last();
        if (await locator.count()) {
          await locator.waitFor({ state: 'visible', timeout: 500 });
          return locator;
        }
      } catch (err) {
        lastError = err;
      }
    }
    await page.waitForTimeout(250);
  }
  throw lastError || new Error(`selector not found: ${selectors.join(', ')}`);
}

async function readLastText(page, selectors) {
  for (let i = 0; i < 20; i += 1) {
    for (const selector of selectors) {
      const values = await page.locator(selector).allTextContents().catch(() => []);
      const text = values.map(v => v.trim()).filter(Boolean).at(-1);
      if (text) return text;
    }
    await page.waitForTimeout(500);
  }
  return '';
}

