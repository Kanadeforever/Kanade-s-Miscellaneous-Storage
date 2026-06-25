// ==UserScript==
// @name         Steam Language Switcher
// @namespace    https://github.com/Kanadeforever
// @version      1.1.6-fix
// @description  在 Steam 页面右上角添加语言切换条（简中/繁中/日本語/EN），可拖拽，自动记忆位置（widget 除外），窄窗口自动压缩
// @author       Luminous
// @match        https://store.steampowered.com/*
// @match        https://steamcommunity.com/*
// @match        https://help.steampowered.com/*
// @match        https://steampowered.com/*
// @grant        GM_registerMenuCommand
// @downloadURL  https://github.com/Kanadeforever/Kanade-s-Miscellaneous-Storage/raw/main/Browser%20Scripts/SteamLanguageSwitcher.user.js
// @updateURL    https://github.com/Kanadeforever/Kanade-s-Miscellaneous-Storage/raw/main/Browser%20Scripts/SteamLanguageSwitcher.user.js
// @run-at       document-end
// ==/UserScript==

(function () {
    'use strict';

    const LANGS = [
        { code: 'schinese',  label: '简中' },
        { code: 'tchinese',  label: '繁中' },
        { code: 'japanese',  label: '日本語' },
        { code: 'english',   label: 'EN' },
    ];

    function getCurrentLang() {
        const m = location.search.match(/[?&]l=([a-z]+)/i);
        return m ? m[1] : null;
    }

    function switchLang(code) {
        const url = new URL(location.href);
        url.searchParams.set('l', code);
        location.href = url.toString();
    }

    GM_registerMenuCommand('重置保存的位置', () => {
        localStorage.removeItem('steam-lang-bar-pos');
        location.reload();
    });

    function buildUI() {
        const cur = getCurrentLang();

        const bar = document.createElement('div');
        bar.id = 'steam-lang-bar';

        // widget 不使用商店坐标，窄窗口自动压缩
        const isWidget = location.pathname.startsWith('/widget/');
        let saved = null;
        if (!isWidget) {
            try { saved = JSON.parse(localStorage.getItem('steam-lang-bar-pos')); } catch (e) {}
            if (saved && (saved.x > window.innerWidth - 50 || saved.y > window.innerHeight - 50)) saved = null;
        }
        const compact = isWidget || window.innerWidth < 480 || window.innerHeight < 300;
        const cssBase = 'position: fixed; ' +
            (saved ? 'left: ' + saved.x + 'px; top: ' + saved.y + 'px;' : 'top: 6px; right: 16px;') +
            'z-index: 99999; display: flex; gap: ' + (compact ? '1px' : '4px') + ';' +
            'padding: ' + (compact ? '2px 3px' : '6px 8px') + ';' +
            'background: rgba(23, 26, 33, 0.92); border-radius: ' + (compact ? '4px' : '8px') + ';' +
            'box-shadow: 0 2px 12px rgba(0,0,0,0.4);' +
            'font-family: "Motiva Sans", Arial, sans-serif; font-size: ' + (compact ? '11px' : '13px') + ';' +
            'user-select:none; cursor:move;';

        bar.style.cssText = cssBase;

        // ---- 拖拽手柄 ----
        const handle = document.createElement('span');
        handle.textContent = '⋮';
        handle.title = '拖拽移动';
        handle.style.cssText = 'display:flex;align-items:center;padding:0 4px;cursor:grab;color:#888;font-size:' +
            (compact ? '13px' : '15px') + ';line-height:1;user-select:none';
        bar.prepend(handle);

        // ---- 拖拽 ----
        let dragging = false, ox, oy;
        bar.addEventListener('mousedown', e => {
            if (e.target !== bar && e.target !== handle) return;
            dragging = true;
            const rect = bar.getBoundingClientRect();
            ox = e.clientX - rect.left;
            oy = e.clientY - rect.top;
            bar.style.right = 'auto';  // 切换为 left/top 定位
            bar.style.left = rect.left + 'px';
        });
        document.addEventListener('mousemove', e => {
            if (!dragging) return;
            bar.style.left = (e.clientX - ox) + 'px';
            bar.style.top  = (e.clientY - oy) + 'px';
        });
        document.addEventListener('mouseup', () => {
            if (!dragging) return;
            dragging = false;
            try {
                if (!isWidget) {  // widget 不保存坐标，避免污染商店位置
                    localStorage.setItem('steam-lang-bar-pos', JSON.stringify({
                        x: parseInt(bar.style.left),
                        y: parseInt(bar.style.top)
                    }));
                }
            } catch (e) {}
        });

        for (const l of LANGS) {
            const btn = document.createElement('button');
            btn.textContent = l.label;
            btn.title = l.label;
            btn.style.cssText = [
                'padding: ' + (compact ? '2px 5px' : '5px 10px') + '; border: none; border-radius: 4px;',
                'background: ' + (l.code === cur ? 'rgba(255,255,255,0.18)' : 'transparent') + ';',
                'color: ' + (l.code === cur ? '#fff' : '#b0b0b0') + ';',
                'cursor: pointer; white-space: nowrap;',
                'transition: background 0.15s, color 0.15s;',
            ].join('');

            btn.addEventListener('mouseenter', () => {
                btn.style.background = 'rgba(255,255,255,0.12)';
                btn.style.color = '#fff';
            });
            btn.addEventListener('mouseleave', () => {
                const stillCurrent = getCurrentLang() === l.code;
                btn.style.background = stillCurrent ? 'rgba(255,255,255,0.18)' : 'transparent';
                btn.style.color = stillCurrent ? '#fff' : '#b0b0b0';
            });

            btn.addEventListener('click', () => switchLang(l.code));
            bar.appendChild(btn);
        }

        // 小叉关闭按钮
        const closeBtn = document.createElement('button');
        closeBtn.textContent = '✕';
        closeBtn.title = '隐藏切换栏';
        closeBtn.style.cssText = [
            'padding: ' + (compact ? '2px 4px' : '5px 6px') + '; border: none; border-radius: 4px;',
            'background: transparent; color: #666;',
            'cursor: pointer; font-size: ' + (compact ? '10px' : '12px') + ';',
            'transition: color 0.15s;',
        ].join('');
        closeBtn.addEventListener('mouseenter', () => { closeBtn.style.color = '#e44'; });
        closeBtn.addEventListener('mouseleave', () => { closeBtn.style.color = '#666'; });
        closeBtn.addEventListener('click', () => {
            bar.remove();
        });
        bar.appendChild(closeBtn);

        document.body.appendChild(bar);
    }

    if (document.body) {
        buildUI();
    } else {
        const obs = new MutationObserver(() => {
            if (document.body) { obs.disconnect(); buildUI(); }
        });
        obs.observe(document.documentElement, { childList: true });
    }
})();
