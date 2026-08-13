import { chromium } from 'playwright-core';

async function main() {
  const statusRes = await fetch('http://127.0.0.1:17248/profiles/20f77a2f-c56a-46b3-b318-ee701793f459/status');
  const statusJson = await statusRes.json();
  
  if (!statusJson.ok || !statusJson.data || !statusJson.data.cdpEndpoint) {
    throw new Error('Profile is not running');
  }
  
  const endpoint = statusJson.data.cdpEndpoint;
  const browser = await chromium.connectOverCDP(endpoint);
  const context = browser.contexts()[0];
  const page = context.pages()[0] || await context.newPage();
  
  console.log('Navigating to Outlook...');
  await page.goto('https://outlook.live.com', { waitUntil: 'load', timeout: 60000 });
  
  // Check if we are already logged in or if we need to click Sign In
  const currentUrl = page.url();
  console.log('Current URL:', currentUrl);
  
  if (currentUrl.includes('mail.live.com') || currentUrl.includes('outlook.office.com') || currentUrl.includes('outlook.live.com/mail')) {
    console.log('Already logged in!');
    await browser.close();
    return;
  }
  
  // Look for sign in button on the landing page if not redirected
  const signInButton = page.locator('a:has-text("Sign in"), a:has-text("Войти")').first();
  if (await signInButton.isVisible()) {
    console.log('Clicking sign-in button...');
    await signInButton.click();
    await page.waitForLoadState('load');
  }
  
  // Wait for email input
  console.log('Waiting for login form...');
  const emailInput = page.locator('input[type="email"], input[name="loginfmt"]');
  await emailInput.waitFor({ state: 'visible', timeout: 30000 });
  
  console.log('Entering email...');
  await emailInput.fill('ristarel1@outlook.com');
  
  const nextBtn = page.locator('input[type="submit"], button[type="submit"], #idSIButton9');
  await nextBtn.click();
  
  // Wait for password or check for captcha / errors
  console.log('Waiting for password input...');
  const passwordInput = page.locator('input[type="password"], input[name="passwd"]');
  try {
    await passwordInput.waitFor({ state: 'visible', timeout: 10000 });
  } catch (err) {
    console.log('Password input not visible immediately. Checking for verification, phone code or captcha.');
    const errorMsg = page.locator('#usernameError, #loginHeader');
    if (await errorMsg.isVisible()) {
      console.log('Header/Error state:', await errorMsg.innerText());
    }
    // Let's print page URL and dump screenshot
    console.log('Current page title:', await page.title());
    await browser.close();
    return;
  }
  
  console.log('Entering password...');
  await passwordInput.fill('Professor.2000');
  
  console.log('Clicking sign in submit...');
  const signInSubmit = page.locator('input[type="submit"], button[type="submit"], #idSIButton9');
  await signInSubmit.click();
  
  // Handle "Stay signed in?" prompt if it appears
  console.log('Checking for "Stay signed in?" screen...');
  const staySignedInSubmit = page.locator('input[type="submit"]#idSIButton9, #KmsiCheckboxField');
  try {
    await staySignedInSubmit.waitFor({ state: 'visible', timeout: 10000 });
    console.log('Found Stay Signed In option. Clicking Yes...');
    const yesBtn = page.locator('input[type="submit"]#idSIButton9');
    await yesBtn.click();
  } catch (err) {
    console.log('Stay signed in screen did not appear, or took too long.');
  }
  
  // Wait for Inbox
  console.log('Waiting for Inbox page to load...');
  try {
    await page.waitForURL(/outlook\.live\.com\/mail/, { timeout: 30000 });
    console.log('Inbox loaded! Current URL:', page.url());
  } catch (err) {
    console.log('Inbox did not load within timeout. Checking page URL:', page.url());
    console.log('Checking if verification is needed (phone/email code):');
    const headerText = await page.locator('h1, h2, #loginHeader').first().innerText().catch(() => 'None');
    console.log('Header text on current page:', headerText);
  }
  
  await browser.close();
}

main().catch(console.error);
