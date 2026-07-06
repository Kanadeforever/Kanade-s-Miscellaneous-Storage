// ==UserScript==
// @name         WNACG 目录类别筛选器
// @namespace    local.wnacg.category-filter
// @version      1.1.0
// @description  在 WNACG 目录页按图片右上角伪元素类别筛选作品
// @match        *://www.wnacg.com/albums-index*.html*
// @match        *://wnacg.com/albums-index*.html*
// @match        *://www.wn07.cfd/albums-index*.html*
// @match        *://www.wn07.shop/albums-index*.html*
// @match        *://www.wn06.cfd/albums-index*.html*
// @match        *://www.wn06.shop/albums-index*.html*
// @match        *://www.wnacg.com/albums.html*
// @match        *://wnacg.com/albums.html*
// @match        *://www.wn07.cfd/albums.html*
// @match        *://www.wn07.shop/albums.html*
// @match        *://www.wn06.cfd/albums.html*
// @match        *://www.wn06.shop/albums.html*
// @match        *://www.wnacg.com/search/?q=*
// @match        *://wnacg.com/search/?q=*
// @match        *://www.wn07.cfd/search/?q=*
// @match        *://www.wn07.shop/search/?q=*
// @match        *://www.wn06.cfd/search/?q=*
// @match        *://www.wn06.shop/search/?q=*
// @grant        none
// @downloadURL  https://github.com/Kanadeforever/Kanade-s-Miscellaneous-Storage/raw/main/Browser%20Scripts/wnacg-category-filter.user.js
// @updateURL    https://github.com/Kanadeforever/Kanade-s-Miscellaneous-Storage/raw/main/Browser%20Scripts/wnacg-category-filter.user.js
// @run-at       document-idle
// ==/UserScript==

(() => {
  'use strict';

  const PANEL_ID = 'wnacg-category-filter-panel';
  const STYLE_ID = 'wnacg-category-filter-style';
  const HIDDEN_CLASS = 'wnacg-category-filter-hidden';
  const POSITION_KEY = 'wnacg-category-filter-position-v1';

  let autoScanTimer = null;
  let observer = null;

  const CATEGORY_WORDS = [
    '同人誌', '同人志', '同人',
    '單行本', '单行本',
    '雜誌', '杂志',
    '短篇',
    '漢化', '汉化',
    '日語', '日语',
    'English',
    'CG畫集', 'CG画集', 'CG',
    'AI圖集', 'AI图集', 'AI',
    '3D漫畫', '3D漫画', '3D',
    'Cosplay', 'COSPLAY',
    '韓漫', '韩漫',
    '其他', 'その他'
  ];

  function addStyle() {
    if (document.getElementById(STYLE_ID)) return;

    const style = document.createElement('style');
    style.id = STYLE_ID;
    style.textContent = `
      #${PANEL_ID} {
        position: fixed;
        top: 10px;
        left: 50%;
        z-index: 999999;
        display: flex;
        align-items: center;
        gap: 5px;
        padding: 6px 8px;
        border-radius: 8px;
        background: rgba(255, 255, 255, 0.94);
        box-shadow: 0 2px 10px rgba(0, 0, 0, .22);
        color: #333;
        font-size: 13px;
        line-height: 1.4;
        user-select: none;
        touch-action: none;
      }

      #${PANEL_ID} .wnacg-drag-handle {
        display: inline-flex;
        align-items: center;
        justify-content: center;
        width: 14px;
        height: 24px;
        color: #777;
        font-size: 18px;
        line-height: 1;
        cursor: move;
      }

      #${PANEL_ID} .wnacg-panel-title {
        cursor: move;
        white-space: nowrap;
      }

      #${PANEL_ID} select {
        font-size: 13px;
        max-width: 145px;
      }

      #${PANEL_ID} button {
        width: 24px;
        height: 24px;
        padding: 0;
        border: 1px solid #bbb;
        border-radius: 5px;
        background: #f7f7f7;
        color: #333;
        font-size: 15px;
        line-height: 1;
        cursor: pointer;
      }

      #${PANEL_ID} button:hover {
        background: #eee;
      }

      #${PANEL_ID} label {
        display: inline-flex;
        align-items: center;
        gap: 3px;
        cursor: pointer;
        user-select: none;
        white-space: nowrap;
      }

      #${PANEL_ID} input[type="checkbox"] {
        margin: 0;
      }

      .${HIDDEN_CLASS} {
        display: none !important;
      }
    `;
    document.head.appendChild(style);
  }

  function clamp(value, min, max) {
    return Math.min(Math.max(value, min), max);
  }

  function getPanelRectLimit(panel) {
    const margin = 6;
    const width = panel.offsetWidth || 220;
    const height = panel.offsetHeight || 36;

    return {
      minX: margin,
      minY: margin,
      maxX: Math.max(margin, window.innerWidth - width - margin),
      maxY: Math.max(margin, window.innerHeight - height - margin)
    };
  }

  function setPanelPosition(panel, x, y, save = true) {
    const limit = getPanelRectLimit(panel);

    const fixedX = clamp(x, limit.minX, limit.maxX);
    const fixedY = clamp(y, limit.minY, limit.maxY);

    panel.style.left = `${fixedX}px`;
    panel.style.top = `${fixedY}px`;
    panel.style.right = 'auto';
    panel.style.transform = 'none';

    if (save) {
      localStorage.setItem(POSITION_KEY, JSON.stringify({
        x: fixedX,
        y: fixedY
      }));
    }
  }

  function findResetAnchor() {
    const selectors = [
      '.wrap',
      '.wrapper',
      '.container',
      '#container',
      '#main',
      '.main',
      '#content',
      '.content',
      '.gallary_wrap',
      '.gallery_wrap',
      '.gallary',
      '.gallery'
    ];

    for (const selector of selectors) {
      const el = document.querySelector(selector);
      if (!el) continue;

      const rect = el.getBoundingClientRect();
      if (rect.width >= 300 && rect.height >= 40) {
        return rect;
      }
    }

    const cards = findAlbumCards();
    if (cards.length) {
      const parent = cards[0].parentElement;
      if (parent) {
        const rect = parent.getBoundingClientRect();
        if (rect.width >= 300) return rect;
      }
    }

    return {
      left: 0,
      top: 0,
      width: window.innerWidth,
      height: window.innerHeight
    };
  }

  function resetPanelPosition(panel, save = true) {
    const anchor = findResetAnchor();
    const x = Math.round(anchor.left + (anchor.width - panel.offsetWidth) / 2);
    const y = 10;

    setPanelPosition(panel, x, y, save);
  }

  function restorePanelPosition(panel) {
    let pos = null;

    try {
      pos = JSON.parse(localStorage.getItem(POSITION_KEY) || 'null');
    } catch (_) {
      pos = null;
    }

    if (
      pos &&
      Number.isFinite(pos.x) &&
      Number.isFinite(pos.y)
    ) {
      setPanelPosition(panel, pos.x, pos.y, false);
    } else {
      resetPanelPosition(panel, false);
    }
  }

  function keepPanelInsideViewport(panel) {
    const rect = panel.getBoundingClientRect();
    setPanelPosition(panel, rect.left, rect.top, true);
  }

  function enableDrag(panel) {
    let dragging = false;
    let startClientX = 0;
    let startClientY = 0;
    let startLeft = 0;
    let startTop = 0;

    function isControl(target) {
      return Boolean(target.closest('select, button, input, label, option'));
    }

    panel.addEventListener('pointerdown', event => {
      if (event.button !== 0) return;
      if (isControl(event.target)) return;

      dragging = true;
      panel.setPointerCapture(event.pointerId);

      const rect = panel.getBoundingClientRect();
      startClientX = event.clientX;
      startClientY = event.clientY;
      startLeft = rect.left;
      startTop = rect.top;

      event.preventDefault();
    });

    panel.addEventListener('pointermove', event => {
      if (!dragging) return;

      const nextX = startLeft + event.clientX - startClientX;
      const nextY = startTop + event.clientY - startClientY;

      setPanelPosition(panel, nextX, nextY, false);
    });

    panel.addEventListener('pointerup', event => {
      if (!dragging) return;

      dragging = false;

      const rect = panel.getBoundingClientRect();
      setPanelPosition(panel, rect.left, rect.top, true);

      try {
        panel.releasePointerCapture(event.pointerId);
      } catch (_) {}
    });

    panel.addEventListener('pointercancel', () => {
      if (!dragging) return;

      dragging = false;

      const rect = panel.getBoundingClientRect();
      setPanelPosition(panel, rect.left, rect.top, true);
    });

    window.addEventListener('resize', () => {
      keepPanelInsideViewport(panel);
    });

    window.addEventListener('scroll', () => {
      keepPanelInsideViewport(panel);
    }, { passive: true });
  }

  function decodeCssString(text) {
    return text.replace(/\\([0-9a-fA-F]{1,6}\s?|.)/g, (_, esc) => {
      const hex = esc.trim();
      if (/^[0-9a-fA-F]+$/.test(hex)) {
        return String.fromCodePoint(parseInt(hex, 16));
      }
      return esc;
    });
  }

  function cleanContent(value) {
    if (!value) return '';
    if (value === 'none' || value === 'normal') return '';

    let text = value.trim();

    if (!text || text === '""' || text === "''") return '';
    if (/^url\(/i.test(text)) return '';

    if (
      (text.startsWith('"') && text.endsWith('"')) ||
      (text.startsWith("'") && text.endsWith("'"))
    ) {
      text = text.slice(1, -1);
    }

    return decodeCssString(text)
      .replace(/\\a/gi, '\n')
      .replace(/\\"/g, '"')
      .replace(/\\'/g, "'")
      .replace(/\s+/g, ' ')
      .trim();
  }

  function isPossibleCategory(text) {
    if (!text) return false;
    if (text.length > 24) return false;

    if (/^[\d\s.,:;!?~～|/\\()[\]{}<>「」『』【】+\-*_=#@$%^&]+$/.test(text)) {
      return false;
    }

    const badWords = [
      'new', 'hot', 'view', 'views',
      'page', 'next', 'prev',
      '上一頁', '下一頁', '上一页', '下一页'
    ];

    if (badWords.includes(text.toLowerCase())) return false;

    return true;
  }

  function scorePseudoCandidate(el, pseudo, text, style) {
    let score = 0;

    const classAndId = `${el.className || ''} ${el.id || ''}`.toLowerCase();

    if (CATEGORY_WORDS.some(word => text.toLowerCase().includes(word.toLowerCase()))) {
      score += 80;
    }

    if (/cate|cat|type|tag|label|sort|mark|lang|language|class/.test(classAndId)) {
      score += 35;
    }

    if (style.position === 'absolute' || style.position === 'fixed') {
      score += 20;
    }

    if (style.top !== 'auto') score += 8;
    if (style.right !== 'auto') score += 12;

    const bg = style.backgroundColor;
    if (bg && bg !== 'transparent' && bg !== 'rgba(0, 0, 0, 0)') {
      score += 5;
    }

    if (pseudo === '::before') score += 3;

    if (text.length <= 6) score += 8;
    if (text.length > 12) score -= 10;

    return score;
  }

  function readPseudo(el, pseudo) {
    const style = window.getComputedStyle(el, pseudo);
    const text = cleanContent(style.content);
    return { style, text };
  }

  function extractCategoryFromCard(card) {
    const elements = [card, ...card.querySelectorAll('*')];
    const candidates = [];

    for (const el of elements) {
      for (const pseudo of ['::before', '::after']) {
        const { style, text } = readPseudo(el, pseudo);
        if (!isPossibleCategory(text)) continue;

        candidates.push({
          text,
          score: scorePseudoCandidate(el, pseudo, text, style),
          el,
          pseudo
        });
      }
    }

    if (!candidates.length) return '';

    candidates.sort((a, b) => b.score - a.score);
    return candidates[0].text;
  }

  function findAlbumCards() {
    const albumLinks = [
      ...document.querySelectorAll(
        'a[href*="photos-index"], a[href*="photos-gallery"], a[href*="photos-slide"]'
      )
    ];

    const cards = albumLinks.map(link => {
      return (
        link.closest('li') ||
        link.closest('.gallary_item') ||
        link.closest('.gallery_item') ||
        link.closest('.pic_box') ||
        link.closest('.album') ||
        link.closest('.item') ||
        link.closest('div') ||
        link.parentElement
      );
    }).filter(Boolean);

    return [...new Set(cards)];
  }

  function scan() {
    const cards = findAlbumCards();

    for (const card of cards) {
      const category = extractCategoryFromCard(card);
      card.dataset.wnacgCategory = category || '__unknown__';
    }

    console.table(cards.map(card => ({
      category: card.dataset.wnacgCategory,
      text: card.innerText.trim().replace(/\s+/g, ' ').slice(0, 100)
    })));

    return cards;
  }

  function buildOptions(select, cards) {
    const currentValue = select.value || '__all__';

    const countMap = new Map();

    for (const card of cards) {
      const category = card.dataset.wnacgCategory || '__unknown__';
      countMap.set(category, (countMap.get(category) || 0) + 1);
    }

    const entries = [...countMap.entries()].sort((a, b) => {
      if (a[0] === '__unknown__') return 1;
      if (b[0] === '__unknown__') return -1;
      return a[0].localeCompare(b[0], 'zh-Hant');
    });

    select.textContent = '';

    const all = document.createElement('option');
    all.value = '__all__';
    all.textContent = `全部（${cards.length}）`;
    select.appendChild(all);

    for (const [category, count] of entries) {
      const option = document.createElement('option');
      option.value = category;
      option.textContent = `${category === '__unknown__' ? '未识别' : category}（${count}）`;
      select.appendChild(option);
    }

    if ([...select.options].some(option => option.value === currentValue)) {
      select.value = currentValue;
    } else {
      select.value = '__all__';
    }
  }

  function applyFilter(value) {
    const cards = findAlbumCards();

    for (const card of cards) {
      const category = card.dataset.wnacgCategory || '__unknown__';
      const shouldShow = value === '__all__' || category === value;
      card.classList.toggle(HIDDEN_CLASS, !shouldShow);
    }
  }

  function rescanAndRefresh(select) {
    const cards = scan();
    buildOptions(select, cards);
    applyFilter(select.value);

    const panel = document.getElementById(PANEL_ID);
    if (panel) keepPanelInsideViewport(panel);
  }

  function scheduleAutoScan(select, delay = 500) {
    clearTimeout(autoScanTimer);

    autoScanTimer = setTimeout(() => {
      const autoCheckbox = document.querySelector(`#${PANEL_ID} input[type="checkbox"]`);
      if (!autoCheckbox || !autoCheckbox.checked) return;

      rescanAndRefresh(select);
    }, delay);
  }

  function startAutoObserver(select) {
    if (observer) observer.disconnect();

    observer = new MutationObserver(mutations => {
      const panel = document.getElementById(PANEL_ID);

      const shouldIgnore = mutations.every(mutation => {
        if (panel && panel.contains(mutation.target)) return true;

        return [...mutation.addedNodes, ...mutation.removedNodes].every(node => {
          return node.nodeType === 1 && panel && panel.contains(node);
        });
      });

      if (shouldIgnore) return;

      scheduleAutoScan(select);
    });

    observer.observe(document.body, {
      childList: true,
      subtree: true
    });
  }

  function createPanel() {
    if (document.getElementById(PANEL_ID)) return;

    addStyle();

    const panel = document.createElement('div');
    panel.id = PANEL_ID;

    const dragHandle = document.createElement('span');
    dragHandle.className = 'wnacg-drag-handle';
    dragHandle.textContent = '⋮';
    dragHandle.title = '拖动面板';

    const label = document.createElement('span');
    label.className = 'wnacg-panel-title';
    label.textContent = '类别';
    label.title = '拖动面板';

    const select = document.createElement('select');

    const refresh = document.createElement('button');
    refresh.type = 'button';
    refresh.textContent = '↺';
    refresh.title = '重扫当前页';

    const reset = document.createElement('button');
    reset.type = 'button';
    reset.textContent = '◎';
    reset.title = '复位到顶部居中';

    const autoLabel = document.createElement('label');

    const autoCheckbox = document.createElement('input');
    autoCheckbox.type = 'checkbox';
    autoCheckbox.checked = true;

    const autoText = document.createElement('span');
    autoText.textContent = '自动';

    autoLabel.append(autoCheckbox, autoText);

    select.addEventListener('change', () => {
      applyFilter(select.value);
    });

    refresh.addEventListener('click', () => {
      rescanAndRefresh(select);
    });

    reset.addEventListener('click', () => {
      resetPanelPosition(panel, true);
    });

    autoCheckbox.addEventListener('change', () => {
      if (autoCheckbox.checked) {
        rescanAndRefresh(select);
      }
    });

    panel.append(dragHandle, label, select, refresh, reset, autoLabel);
    document.body.appendChild(panel);

    restorePanelPosition(panel);
    enableDrag(panel);

    rescanAndRefresh(select);
    startAutoObserver(select);

    setTimeout(() => {
      restorePanelPosition(panel);
      scheduleAutoScan(select, 0);
    }, 800);

    setTimeout(() => scheduleAutoScan(select, 0), 1800);
    setTimeout(() => scheduleAutoScan(select, 0), 3500);
  }

  createPanel();
})();
