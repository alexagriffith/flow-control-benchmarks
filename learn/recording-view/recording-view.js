/* Presentation adapter only. The learner remains the owner of all scenes and controls. */
(() => {
  'use strict';
  const frame = document.querySelector('#learner');
  const tools = document.querySelector('#tools');
  const chapter = document.querySelector('#chapter');
  const params = new URLSearchParams(location.search);
  let doc;
  const pageNumber = () => Number((frame.contentWindow.location.hash.match(/^#page-(\d+)$/) || [0, 0])[1]);
  const showTools = () => { tools.dataset.hidden = 'false'; };
  const sync = () => {
    const page = pageNumber();
    doc.body.dataset.recordingScene = page === 14 ? 'replay' : page >= 11 && page <= 13 ? 'evidence' : 'diagram';
    doc.body.dataset.recordingHeading = page < 6 || (page >= 11 && page <= 13) ? 'embedded' : 'external';
    chapter.value = String(page);
    const kind = doc.querySelector('#stepLab').textContent.split(' · ').slice(1).join(' · ');
    doc.querySelector('#topbar .t span').textContent = kind || 'Interactive';
    document.querySelector('#previous').disabled = doc.querySelector('#backBtn').disabled;
    document.querySelector('#next').disabled = doc.querySelector('#nextBtn').disabled;
    document.querySelector('#normal').href = '../flow-control-journey.html#page-' + page;
    history.replaceState(null, '', '?page=' + page + (tools.dataset.hidden === 'true' ? '&clean=1' : ''));
  };
  const hideTools = () => {
    tools.dataset.hidden = 'true';
    // Do not call window.focus: the learner has its own function with that name.
    document.activeElement?.blur();
    frame.focus();
    doc.querySelector('#main').focus({preventScroll:true});
    sync();
  };
  const escape = event => {
    if (event.key === 'Escape') { showTools(); sync(); document.querySelector('#clean').focus(); }
  };
  document.addEventListener('keydown', escape);
  frame.addEventListener('load', async () => {
    try {
      doc = frame.contentDocument;
      const required = ['#app','#main','#canvasWrap','#side','#pTitle','#narr','#backBtn','#nextBtn','#rail','#autoBtn','#evidence','#playCtl'];
      if (!doc || required.some(selector => !doc.querySelector(selector))) throw new Error('The learner layout changed. Open the regular learner; recording view needs its adapter updated.');
      const style = doc.createElement('link');
      style.rel = 'stylesheet'; style.href = new URL('recording-view.css', location.href).href;
      await new Promise((resolve, reject) => {
        style.onload = resolve;
        style.onerror = () => reject(new Error('Recording styles could not load. Reload this page when the server is available.'));
        doc.head.append(style);
      });
      doc.documentElement.dataset.theme = 'dark';
      doc.body.classList.add('recording-view');
      if (doc.querySelector('#autoBtn').getAttribute('aria-pressed') === 'true') doc.querySelector('#autoBtn').click();
      chapter.replaceChildren();
      doc.querySelectorAll('#rail button[aria-label]').forEach(button => {
        const match = button.getAttribute('aria-label').match(/^Go to page (\d+): (.*)$/);
        if (!match) return;
        const option = new Option(`${Number(match[1])+1} · ${match[2]}`, match[1]); chapter.add(option);
      });
      chapter.onchange = () => doc.querySelector(`#rail button[aria-label^="Go to page ${chapter.value}:"]`).click();
      document.querySelector('#previous').onclick = () => doc.querySelector('#backBtn').click();
      document.querySelector('#next').onclick = () => doc.querySelector('#nextBtn').click();
      document.querySelector('#clean').onclick = hideTools;
      doc.addEventListener('keydown', escape);
      frame.contentWindow.addEventListener('hashchange', sync);
      new MutationObserver(sync).observe(doc.querySelector('#narr'), {childList:true,subtree:true,characterData:true});
      sync();
      if (params.get('clean') === '1') hideTools();
      document.documentElement.dataset.ready = 'true';
    } catch (error) {
      const alert = document.querySelector('#error'); alert.textContent = error.message; alert.hidden = false;
    }
  });
  const requested = Number(params.get('page') || 0);
  const page = Number.isInteger(requested) ? Math.max(0, Math.min(14, requested)) : 0;
  frame.src = '../flow-control-journey.html#page-' + page;
})();
