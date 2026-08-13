import googleLogin from './googleLogin.js';
import ggselLogin from './ggselLogin.js';
import botSannysoft from './botSannysoft.js';
import chatgptLogin from './chatgptLogin.js';
import { makeAiProviderScenario } from './aiProviderSend.js';

const registry = {
  'google-login': googleLogin,
  'ggsel-login': ggselLogin,
  'bot-sannysoft': botSannysoft,
  'chatgpt-login': chatgptLogin,
  'chatgpt-send': makeAiProviderScenario('chatgpt'),
  'claude-send': makeAiProviderScenario('claude'),
  'gemini-send': makeAiProviderScenario('gemini'),
  'minimax-send': makeAiProviderScenario('minimax'),
};

export function runScenario(name, ctx) {
  const fn = registry[name];
  if (!fn) throw new Error(`Unknown scenario: ${name}`);
  return fn(ctx);
}

export function listScenarios() {
  return Object.keys(registry);
}
