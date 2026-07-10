/* Polaris FLOW — shared /stream/events connection (Jin 2026-07-10,
 * feat/cloud-river). Before this file, /flow opened up to 3 independent
 * EventSource connections to the same endpoint (flow.js's river particles,
 * wall.js's director camera/killcam, globe-flows.js's gate feed when the
 * globe was still loaded on this page). The globe is gone now (replaced by
 * cloud.js) — this single shared connection is the page's ONLY
 * /stream/events socket. flow.js and cloud.js both subscribe via
 * window.PolarisEvents.on(fn) instead of opening their own EventSource.
 *
 * Display-only: forwards the raw parsed SSE payload (``{events:[...]}``
 * and/or ``{gate_events:[...]}``, same shape the server already emits) to
 * every subscriber, unmodified. Loaded before flow.js/cloud.js.
 */
(function () {
  const listeners = [];

  function connect() {
    let es;
    try {
      es = new EventSource('/stream/events');
    } catch (e) {
      return;
    }
    es.onmessage = (ev) => {
      let payload;
      try {
        payload = JSON.parse(ev.data);
      } catch (e) {
        return;
      }
      for (const fn of listeners) {
        try {
          fn(payload);
        } catch (e) {
          /* one bad subscriber must not break the others */
        }
      }
    };
    es.onerror = () => {
      /* EventSource auto-reconnects */
    };
  }

  window.PolarisEvents = {
    on: (fn) => {
      listeners.push(fn);
    },
  };
  connect();
})();
