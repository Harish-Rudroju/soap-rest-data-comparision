// Cinematic "Start Engine" animation controller.
// Fully isolated from the rest of the app's JS -- only touches elements
// inside #engine-stage.
//
// Design note: the real /run request is fired immediately on click (via
// fetch, in the background) -- that's what makes this honest. The
// animation is not a fake timer gating a request that hasn't started yet;
// it's just what plays while the (already-in-flight) real request
// finishes. Once both the animation's minimum "show" time AND the actual
// response are ready, we swap the real result in. Tradeoff: since this
// uses fetch() rather than a native navigation, the browser's own
// tab-loading spinner does not appear during this -- only real top-level
// navigations trigger that.
(function () {
  var stage = document.getElementById('engine-stage');
  var btn = document.getElementById('engine-start-btn');
  var form = document.getElementById('run-form');
  if (!stage || !btn || !form) return;

  var RUNNING_LABEL = '✓ Engine Running';

  var PULL_MS = 900;      // sharp pull + recoil
  var HOLD_MS = 600;      // brief settle before the engine reacts
  var START_MS = 750;     // shake/attempt
  var RUN_HOLD_MS = 700;  // how long "running" is shown before the real result takes over

  var hasTriggered = false;

  function setPhase(name) {
    stage.classList.remove('phase-pull', 'phase-start', 'phase-run');
    if (name) stage.classList.add(name);
  }

  function delay(ms) {
    return new Promise(function (resolve) { setTimeout(resolve, ms); });
  }

  btn.addEventListener('click', function (e) {
    e.preventDefault();
    if (hasTriggered) return;
    hasTriggered = true;

    btn.textContent = 'Starting…';
    btn.classList.add('is-igniting');

    var formData = new FormData(form);
    var requestDone = fetch(form.action, { method: 'POST', body: formData })
      .then(function (resp) {
        if (!resp.ok) throw new Error('Request failed: ' + resp.status);
        return resp.text();
      });

    var t = 0;
    setPhase('phase-pull');

    t += PULL_MS + HOLD_MS;
    setTimeout(function () { setPhase('phase-start'); }, t);

    t += START_MS;
    setTimeout(function () {
      setPhase('phase-run');
      btn.textContent = RUNNING_LABEL;
      btn.classList.remove('is-igniting');
      btn.classList.add('is-running');
    }, t);

    var minShow = delay(t + RUN_HOLD_MS);

    Promise.all([requestDone, minShow])
      .then(function (results) {
        var html = results[0];
        document.open();
        document.write(html);
        document.close();
      })
      .catch(function () {
        // If the background request itself fails, fall back to a normal
        // submit so the user isn't stuck looking at a dead button.
        form.submit();
      });
  });
})();
