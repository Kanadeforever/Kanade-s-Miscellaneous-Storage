// ==UserScript==
// @name         Steam Language Switcher
// @namespace    https://github.com/Kanadeforever
// @version      1.0.1
// @description  在 Steam 页面快速切换语言：简中 / 繁中 / 日本語 / English
// @author       Luminous
// @match        https://store.steampowered.com/*
// @match        https://steamcommunity.com/*
// @match        https://help.steampowered.com/*
// @match        https://steampowered.com/*
// @grant        none
// @downloadURL https://github.com/Kanadeforever/Kanade-s-Miscellaneous-Storage/raw/main/Browser%20Scripts/SteamLanguageSwitcher.user.js
// @updateURL https://github.com/Kanadeforever/Kanade-s-Miscellaneous-Storage/raw/main/Browser%20Scripts/SteamLanguageSwitcher.user.js
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

    function buildUI() {
        const cur = getCurrentLang();

        const bar = document.createElement('div');
        bar.id = 'steam-lang-bar';
        bar.style.cssText = [
            'position: fixed; top: 6px; right: 16px; z-index: 99999;',
            'display: flex; gap: 4px; padding: 6px 8px;',
            'background: rgba(23, 26, 33, 0.92); border-radius: 8px;',
            'box-shadow: 0 2px 12px rgba(0,0,0,0.4);',
            'font-family: "Motiva Sans", Arial, sans-serif; font-size: 13px;',
            'user-select: none; cursor: move;',
        ].join('');

        // ---- 拖拽 ----
        let dragging = false, ox, oy;
        bar.addEventListener('mousedown', e => {
            if (e.target !== bar) return;  // 点在按钮上不拖
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
        document.addEventListener('mouseup', () => { dragging = false; });

        for (const l of LANGS) {
            const btn = document.createElement('button');
            btn.textContent = l.label;
            btn.title = l.label;
            btn.style.cssText = [
                'padding: 5px 10px; border: none; border-radius: 4px;',
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
            'padding: 5px 6px; border: none; border-radius: 4px;',
            'background: transparent; color: #666;',
            'cursor: pointer; font-size: 12px;',
            'transition: color 0.15s;',
        ].join('');
        closeBtn.addEventListener('mouseenter', () => { closeBtn.style.color = '#e44'; });
        closeBtn.addEventListener('mouseleave', () => { closeBtn.style.color = '#666'; });
        closeBtn.addEventListener('click', () => {
            bar.remove();
            sessionStorage.setItem('steam-lang-bar-hidden', '1');
        });
        bar.appendChild(closeBtn);

        document.body.appendChild(bar);
    }

    if (sessionStorage.getItem('steam-lang-bar-hidden')) return;

    if (document.body) {
        buildUI();
    } else {
        const obs = new MutationObserver(() => {
            if (document.body) { obs.disconnect(); buildUI(); }
        });
        obs.observe(document.documentElement, { childList: true });
    }
})();
