const form = document.querySelector('#interest-form');
const status = document.querySelector('#status');
const button = form.querySelector('button');
const buttonLabel = button.querySelector('span');
const main = document.querySelector('main');
const intro = document.querySelector('.intro');
const card = document.querySelector('.registration');
const registered = document.querySelector('#registered');
const registeredTitle = document.querySelector('#registered-title');
const confirmationStatus = document.querySelector('#confirmation-status');
const skip = document.querySelector('.skip');
const cookieName = 'lotuspond_interest';
const cookieDomain = ['lotuspond.ai', 'www.lotuspond.ai'].includes(location.hostname)
  ? '; Domain=lotuspond.ai'
  : '';
const secureCookie = location.protocol === 'https:' ? '; Secure' : '';

// This is only a presentation preference, never proof of identity or access.
// No email address or other personal data is stored in the cookie.
function rememberRegistration(remember) {
  try {
    document.cookie = `${cookieName}=${remember ? 'registered' : ''}; Max-Age=${remember ? 15552000 : 0}; Path=/; SameSite=Lax${cookieDomain}${secureCookie}`;
  } catch {
    // Signup still succeeds when the browser blocks cookies.
  }
}
function showRegistered(message = '', focus = false) {
  intro.hidden = true;
  card.hidden = true;
  registered.hidden = false;
  main.classList.add('is-registered');
  document.title = 'Lotuspond · You’re on the list';
  skip.href = '#registered';
  skip.textContent = 'Skip to confirmation';
  confirmationStatus.textContent = message;
  if (focus) registeredTitle.focus();
}
let remembered = false;
try {
  remembered = document.cookie
    .split(';')
    .some((entry) => entry.trim() === `${cookieName}=registered`);
} catch {
  // Cookie access is optional.
}
if (remembered) showRegistered();

document.querySelector('.register-another').addEventListener('click', () => {
  rememberRegistration(false);
  registered.hidden = true;
  intro.hidden = false;
  card.hidden = false;
  main.classList.remove('is-registered');
  document.title = 'Lotuspond · Early access';
  skip.href = '#register';
  skip.textContent = 'Skip to registration';
  form.reset();
  status.textContent = '';
  confirmationStatus.textContent = '';
  form.elements.email.focus();
});

form.addEventListener('submit', async (event) => {
  event.preventDefault();
  if (button.disabled || !form.reportValidity()) return;
  button.disabled = true;
  buttonLabel.textContent = 'Saving your place…';
  status.textContent = '';
  status.dataset.error = 'false';
  try {
    const response = await fetch('/api/interest', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        email: form.elements.email.value,
        consent: document.querySelector('#consent').checked,
        website: form.elements.website.value,
      }),
      signal: AbortSignal.timeout(15000),
    });
    const result = await response.json();
    if (!response.ok)
      throw new Error(result.message || 'We couldn’t save your email. Please try again.');
    rememberRegistration(true);
    form.reset();
    showRegistered(result.message, true);
  } catch (error) {
    status.dataset.error = 'true';
    status.textContent =
      error.name === 'TimeoutError' || error instanceof TypeError
        ? 'We couldn’t confirm your registration. Please try again; your email will only be listed once.'
        : error.message;
    status.focus();
  } finally {
    button.disabled = false;
    buttonLabel.textContent = 'Join the early access list';
  }
});
