/* ===================================================================
   auth.js — session-based gate for the GoodMonk Command Center.

   NOTE ON SECURITY: this is a lightweight client-side gate suitable for an
   internal dashboard on static hosting (InfinityFree). The credentials live in
   the page, so this keeps casual visitors out — it is NOT a substitute for real
   server-side auth. For anything sensitive, put the dashboard behind HTTP basic
   auth (.htaccess) or a real login service.
   =================================================================== */
const Auth = (function () {
  const USER = 'admin';
  const PASS = 'GMSFV';
  const KEY = 'gm_session';

  function login(u, p) {
    if (u === USER && p === PASS) {
      // Session-scoped: clears when the browser tab closes.
      sessionStorage.setItem(KEY, JSON.stringify({ u, t: Date.now() }));
      return true;
    }
    return false;
  }
  function isAuthed() {
    try { return !!JSON.parse(sessionStorage.getItem(KEY)); }
    catch { return false; }
  }
  function logout() {
    sessionStorage.removeItem(KEY);
    window.location.href = 'login.html';
  }
  // Call at the top of a protected page.
  function requireAuth() {
    if (!isAuthed()) window.location.href = 'login.html';
  }
  return { login, isAuthed, logout, requireAuth };
})();
