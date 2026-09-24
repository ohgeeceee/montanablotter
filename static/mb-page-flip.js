/* =========================================================================
   mb-page-flip.js — newspaper page-turn behavior for montanablotter.com
   Activation: only when
     1. the newspaper broadsheet treatment is on (body has .mb-newspaper-body)
     2. the visitor is on a screen large enough that a 3D page turn feels
        right (desktop/tablet landscape), not a touch phone
   Wiring: drop the <script> tag near the bottom of public_page_base.html,
   after the existing public-nav + analytics handlers. No new dependencies.
   ========================================================================= */

(function () {
  'use strict';

  // ---- Feature flags ----

  var body = document.body;
  if (!body) return;

  var newspaperOn = body.classList.contains('mb-newspaper-body');
  if (!newspaperOn) return;

  var prefersReducedMotion = window.matchMedia(
    '(prefers-reduced-motion: reduce)'
  ).matches;
  if (prefersReducedMotion) return;

  var touchPrimary = window.matchMedia(
    '(hover: none) and (pointer: coarse)'
  ).matches;
  if (touchPrimary) return;

  // Only enable the flip on viewports wide enough that the broadsheet
  // has room to turn without clipping awkwardly. Below 980px the
  // newspaper already collapses to a single column and a page turn
  // reads as a layout jank rather than a deliberate effect.
  var widthOkay = window.innerWidth >= 980;
  if (!widthOkay) return;

  // ---- Config ----

  var CLASS_SHELL = 'ng-page-flip-enabled';
  var CLASS_CALM = 'ng-page-flip-calm';
  var CLASS_SHEET = 'ng-page-flip-sheet';
  var CLASS_ENTER = 'ng-flip-enter';
  var CLASS_ACTIVE = 'ng-flip-active';
  var CLASS_ACTIVE_REVERSE = 'ng-flip-active-reverse';

  var FLIP_DURATION_MS = 620;     // matches CSS for the 3D turn
  var FADE_DURATION_MS = 480;
  var POST_FLIP_LOAD_margin_MS = 60; // tiny buffer after transition before navigation

  // Which navigations should feel like turning the page?
  // - Primary nav links (header row)
  // - Secondary nav links (header more panel)
  // - Breadcrumb / in-paper nav anchors that stay inside the site
  // - Footer links that go to another public page
  // We deliberately skip:
  //   - hash-only links (#section)
  //   - external links
  //   - form submits, buttons, JS-driven app links
  var NAV_SELECTOR =
    'a[href^="/"]:not([href*="#"]):not([data-no-flip]):not(.mb-np-user-chip)';

  // ---- Create the animated sheet ----

  var stage = document.querySelector(
    'body.mb-newspaper-body .mb-newspaper-stage'
  );
  if (!stage) return;

  var article = stage.querySelector('article.mb-newspaper');
  if (!article) return;

  var sheet = document.createElement('div');
  sheet.className = CLASS_SHEET;
  sheet.setAttribute('aria-hidden', 'true');
  sheet.setAttribute('aria-label', 'Turning the page');
  article.appendChild(sheet);

  // ---- Helpers ----

  function hideSheetImmediately() {
    sheet.classList.remove(CLASS_ENTER, CLASS_ACTIVE, CLASS_ACTIVE_REVERSE);
    sheet.style.opacity = '';
    sheet.style.transform = '';
  }

  function captureCurrentPageIntoSheet() {
    // Clone the live article content into the sheet so the turning page
    // looks like the page the reader is currently on.
    //
    // We keep the sheet lightweight: clone the inner DOM, but drop
    // script tags and any in-flight UI chrome that would be confusing
    // to see curl away (dropdowns, mobile menu panels, etc.).
    var clone = article.cloneNode(true);

    // Strip interactive transients that should not curl away.
    var stripSelectors = [
      '.mb-np-user-menu',
      '#user-dropdown',
      '#pub-mobile-menu',
      '#public-nav-more-panel',
      'dialog',
      '.mb-np-flash-wrap',
    ];
    stripSelectors.forEach(function (sel) {
      var nodes = clone.querySelectorAll(sel);
      nodes.forEach(function (n) { n.remove(); });
    });

    // Remove script tags so we don't accidentally re-execute anything.
    var scripts = clone.querySelectorAll('script');
    scripts.forEach(function (s) { s.remove(); });

    sheet.innerHTML = '';
    sheet.appendChild(clone);

    // Drop the sheet in front of the live content, fully opaque,
    // flat (no rotation yet).
    sheet.classList.add(CLASS_ENTER);
    sheet.style.opacity = '1';
    sheet.style.transform = 'rotateY(-2deg) translateX(0)';
  }

  function startFlip(reverse) {
    // Choose the direction class.
    sheet.classList.remove(CLASS_ACTIVE, CLASS_ACTIVE_REVERSE);
    // Force reflow so the removal registers before we add the new state.
    void sheet.offsetWidth;

    if (reverse) {
      sheet.classList.add(CLASS_ACTIVE_REVERSE);
    } else {
      sheet.classList.add(CLASS_ACTIVE);
    }
  }

  function navigationShouldFlip(href) {
    if (!href) return false;
    // External link? no.
    if (href.indexOf('://') !== -1 && href.indexOf(location.origin) !== 0) {
      return false;
    }
    // Anchor-only? no.
    if (href.charAt(0) === '#') return false;
    // Explicit opt-out on the link itself.
    if (href.indexOf('data-no-flip') !== -1) return false;
    return true;
  }

  // ---- Wire up nav clicks ----

  function onClick(event) {
    var link = event.target.closest(NAV_SELECTOR);
    if (!link) return;

    var href = link.getAttribute('href');
    if (!navigationShouldFlip(href)) return;

    // Don't fire on the link pointing to the very page we're already on.
    var currentPath = window.location.pathname.replace(/\/+$/, '') || '/';
    var targetPath = href.split('#')[0].replace(/\/+$/, '') || '/';
    if (targetPath === currentPath) return;

    // Only flip same-origin page loads. Everything else (external, mailto,
    // tel, downloads) is left alone.
    if (href.indexOf('://') !== -1 && href.indexOf(location.origin) !== 0) {
      return;
    }

    event.preventDefault();

    // 1. Snapshot the current page into the turning sheet.
    captureCurrentPageIntoSheet();

    // 2. After a hair of time (so the eye registers the paper first),
    //    start the turn.
    window.setTimeout(function () {
      startFlip(false);

      // 3. After the turn completes, let the browser follow the link.
      window.setTimeout(function () {
        // Clean body state before unload.
        hideSheetImmediately();

        // Follow the link. Use location.href so the browser treats it
        // as a normal navigation (back button, history, etc. all work).
        location.href = href;
      }, FLIP_DURATION_MS + 80);
    }, 90);
  }

  // ---- Attach/detach the click listener based on current conditions ----

  var listenerAttached = false;

  function attachListener() {
    if (listenerAttached) return;
    document.addEventListener('click', onClick, true);
    listenerAttached = true;
  }

  function detachListener() {
    if (!listenerAttached) return;
    document.removeEventListener('click', onClick, true);
    listenerAttached = false;
  }

  // ---- Enable or disable flip based on viewport + feature flags ----

  function updateFlipState() {
    var body = document.body;
    if (!body) return;

    var newspaperOn = body.classList.contains('mb-newspaper-body');
    if (!newspaperOn) {
      detachListener();
      body.classList.remove(CLASS_SHELL);
      hideSheetImmediately();
      return;
    }

    var prefersReducedMotion = window.matchMedia(
      '(prefers-reduced-motion: reduce)'
    ).matches;
    if (prefersReducedMotion) {
      detachListener();
      body.classList.remove(CLASS_SHELL);
      hideSheetImmediately();
      return;
    }

    var touchPrimary = window.matchMedia(
      '(hover: none) and (pointer: coarse)'
    ).matches;
    if (touchPrimary) {
      detachListener();
      body.classList.remove(CLASS_SHELL);
      hideSheetImmediately();
      return;
    }

    var widthOkay = window.innerWidth >= 980;
    if (!widthOkay) {
      detachListener();
      body.classList.remove(CLASS_SHELL);
      hideSheetImmediately();
      return;
    }

    // All conditions met — attach listener and mark body
    attachListener();
    body.classList.add(CLASS_SHELL);

    // Calm mode
    if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
      body.classList.add(CLASS_CALM);
    }
  }

  // Initial state
  updateFlipState();

  // ---- Resize / dynamic re-enable ----

  var mql = window.matchMedia('(min-width: 980px)');
  function onWidthChange(e) {
    updateFlipState();
    // Also re-check other flags on resize since they could change
    if (e.matches) {
      // Coming above threshold — re-evaluate everything
      updateFlipState();
    }
  }
  if (mql.addEventListener) {
    mql.addEventListener('change', onWidthChange);
  } else {
    mql.addListener(onWidthChange);
  }

  // Also re-evaluate if body class changes (e.g. newspaper toggles)
  var bodyObserver = new MutationObserver(function() {
    updateFlipState();
  });
  bodyObserver.observe(document.body, { attributes: true, attributeFilter: ['class'] });

  // ---- Clean up on page unload (no-op for normal nav, but cheap insurance) ----
  window.addEventListener('beforeunload', function () {
    hideSheetImmediately();
  });
})();
