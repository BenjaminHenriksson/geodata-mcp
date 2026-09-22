/* Read-only architecture explorer. No network calls or third-party dependencies. */
(() => {
  'use strict';
  const root = document.querySelector('.architecture');
  if (!root) return;
  const flows = JSON.parse(root.dataset.flows);
  const ui = JSON.parse(root.dataset.ui);
  const languageLinks = [...root.querySelectorAll('[data-language]')];
  const nodes = [...root.querySelectorAll('[data-node]')];
  const edges = [...root.querySelectorAll('.diagram-edges g > path')];
  const byId = new Map(nodes.map(node => [node.dataset.node, node]));
  const flowButtons = [...root.querySelectorAll('[data-flow]')];
  const viewport = root.querySelector('.diagram-viewport');
  const size = root.querySelector('.diagram-size');
  const stage = root.querySelector('.diagram-stage');
  const $ = id => root.querySelector('#' + id);
  let flow = 'overview';
  let step = 0;
  let selected = null;
  let zoom = 1;
  let fitting = true;

  function paint() {
    const allEdges = new Set(flow === 'overview' ? [] : flows[flow].steps.flatMap(s => s.edges));
    const currentEdges = new Set(flow === 'overview' ? [] : flows[flow].steps[step].edges);
    const related = edges.filter(edge => selected && [edge.dataset.from, edge.dataset.to].includes(selected));
    const relatedNodes = new Set(related.flatMap(edge => [edge.dataset.from, edge.dataset.to]));
    const flowNodes = new Set(edges.filter(edge => allEdges.has(edge.id.slice(5))).flatMap(edge => [edge.dataset.from, edge.dataset.to]));
    const currentNodes = new Set(edges.filter(edge => currentEdges.has(edge.id.slice(5))).flatMap(edge => [edge.dataset.from, edge.dataset.to]));
    edges.forEach(edge => {
      const id = edge.id.slice(5);
      edge.classList.toggle('is-flow', !selected && allEdges.has(id));
      edge.classList.toggle('is-current', !selected && currentEdges.has(id));
      edge.classList.toggle('is-related', related.includes(edge));
      edge.classList.toggle('is-dim', selected ? !related.includes(edge) : flow !== 'overview' && !allEdges.has(id));
    });
    nodes.forEach(node => {
      const id = node.dataset.node;
      node.setAttribute('aria-pressed', String(id === selected));
      node.classList.toggle('is-flow', !selected && flowNodes.has(id));
      node.classList.toggle('is-current', !selected && currentNodes.has(id));
      node.classList.toggle('is-dim', selected ? id !== selected && !relatedNodes.has(id) : flow !== 'overview' && !flowNodes.has(id));
    });
    $('diagram-caption').textContent = selected ? byId.get(selected).querySelector('strong').textContent + ui.related
      : flow === 'overview' ? ui.caption : flows[flow].description;
  }

  function selectNode(id, updateHash = true) {
    selected = byId.has(id) ? id : null;
    root.querySelectorAll('.component-detail').forEach(detail => { detail.hidden = detail.id !== 'detail-' + selected; });
    $('detail-intro').hidden = Boolean(selected);
    if (updateHash) writeHash();
    paint();
  }

  function showStep(index, updateHash = true) {
    if (flow === 'overview') return;
    const steps = flows[flow].steps;
    step = Math.max(0, Math.min(index, steps.length - 1));
    selectNode(null, false);
    $('step-title').textContent = steps[step].title;
    $('step-text').textContent = steps[step].text;
    $('step-count').textContent = `${ui.step} ${step + 1} ${ui.of} ${steps.length}`;
    $('previous-step').disabled = step === 0;
    $('next-step').disabled = step === steps.length - 1;
    $('flow-steps').querySelectorAll('button').forEach((button, i) => button.setAttribute('aria-pressed', String(i === step)));
    if (updateHash) writeHash();
    paint();
  }

  function selectFlow(id, index = 0, updateHash = true) {
    flow = Object.hasOwn(flows, id) ? id : 'overview';
    step = 0;
    flowButtons.forEach(button => button.setAttribute('aria-pressed', String(button.dataset.flow === flow)));
    $('flow-walkthrough').hidden = flow === 'overview';
    $('flow-steps').replaceChildren();
    selectNode(null, false);
    if (flow !== 'overview') {
      $('flow-title').textContent = flows[flow].title;
      flows[flow].steps.forEach((item, i) => {
        const button = document.createElement('button');
        button.textContent = String(i + 1);
        button.setAttribute('aria-label', `${ui.step} ${i + 1}: ${item.title}`);
        button.title = item.title;
        button.addEventListener('click', () => showStep(i));
        $('flow-steps').append(button);
      });
      showStep(index, false);
    }
    if (updateHash) writeHash();
    paint();
  }

  function writeHash() {
    const params = new URLSearchParams();
    if (flow !== 'overview') { params.set('flow', flow); params.set('step', String(step + 1)); }
    if (selected) params.set('node', selected);
    const hash = params.toString();
    history.replaceState(null, '', location.pathname + location.search + (hash ? '#' + hash : ''));
    updateLanguageLinks();
  }

  function updateLanguageLinks() {
    languageLinks.forEach(link => {
      link.href = '/architecture?lang=' + link.dataset.language + location.hash;
    });
  }

  function readHash() {
    updateLanguageLinks();
    // Leave ordinary in-page anchors alone.
    if (location.hash && !location.hash.includes('=')) return;
    const params = new URLSearchParams(location.hash.slice(1));
    const n = Number(params.get('step') || 1);
    selectFlow(params.get('flow'), Number.isFinite(n) ? Math.trunc(n) - 1 : 0, false);
    selectNode(params.get('node'), false);
  }

  function setZoom(value, fit = false) {
    fitting = fit;
    // Keep labels readable on phones; the viewport then scrolls horizontally.
    zoom = Math.max(.65, Math.min(1.6, value));
    stage.style.transform = `scale(${zoom})`;
    size.style.width = `${1180 * zoom + 24}px`;
    size.style.height = `${780 * zoom + 24}px`;
    $('zoom-value').textContent = `${Math.round(zoom * 100)}%`;
    $('zoom-out').disabled = zoom <= .65;
    $('zoom-in').disabled = zoom >= 1.6;
  }

  function fit() {
    setZoom(Math.min(1, (viewport.clientWidth - 24) / 1180), true);
    viewport.scrollTo({left: 0, top: 0});
  }

  nodes.forEach(node => node.addEventListener('click', () => {
    selectNode(node.dataset.node);
    if (window.matchMedia('(max-width:1080px)').matches) $('component-detail').scrollIntoView({block: 'start'});
  }));
  $('back-to-diagram').addEventListener('click', () => {
    root.querySelector('.diagram-toolbar').scrollIntoView({block: 'start'});
    if (selected) byId.get(selected).focus({preventScroll: true});
  });
  flowButtons.forEach(button => button.addEventListener('click', () => selectFlow(button.dataset.flow)));
  $('clear-selection').addEventListener('click', () => selectNode(null));
  $('previous-step').addEventListener('click', () => showStep(step - 1));
  $('next-step').addEventListener('click', () => showStep(step + 1));
  $('zoom-out').addEventListener('click', () => setZoom(zoom - .15));
  $('zoom-in').addEventListener('click', () => setZoom(zoom + .15));
  $('zoom-fit').addEventListener('click', fit);
  root.addEventListener('keydown', event => {
    if (event.key === 'Escape') selectNode(null);
  });
  root.querySelectorAll('[data-connections]').forEach(list => {
    const id = list.dataset.connections;
    edges.filter(edge => [edge.dataset.from, edge.dataset.to].includes(id)).forEach(edge => {
      const other = edge.dataset.from === id ? edge.dataset.to : edge.dataset.from;
      const item = document.createElement('li');
      const button = document.createElement('button');
      button.textContent = byId.get(other).querySelector('strong').textContent;
      button.addEventListener('click', () => { selectNode(other); byId.get(other).focus({preventScroll: true}); });
      item.append(button, document.createTextNode(edge.dataset.label));
      list.append(item);
    });
  });
  window.addEventListener('hashchange', readHash);
  new ResizeObserver(() => { if (fitting) fit(); }).observe(viewport);
  fit();
  readHash();
})();
