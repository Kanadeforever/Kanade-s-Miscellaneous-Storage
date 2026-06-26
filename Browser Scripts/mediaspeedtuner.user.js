// ==UserScript==
// @name         Media Speed Tuner
// @namespace    https://github.com/Kanadeforever
// @version      1.0.1
// @description  全局视频/音频倍速控制，支持可配置快捷键、OSD 屏幕提示、跨页面速度共享
// @author       Luminous
// @match        *://*/*
// @grant        GM_getValue
// @grant        GM_setValue
// @grant        GM_registerMenuCommand
// @downloadURL  https://github.com/Kanadeforever/Kanade-s-Miscellaneous-Storage/raw/main/Browser%20Scripts/mediaspeedtuner.user.js
// @updateURL    https://github.com/Kanadeforever/Kanade-s-Miscellaneous-Storage/raw/main/Browser%20Scripts/mediaspeedtuner.user.js
// @run-at       document-start
// ==/UserScript==

(function () {
    'use strict';

    // ═══════════════════════════════════════════
    // 默认配置
    // ═══════════════════════════════════════════
    var DEFAULTS = {
        speedStep: 0.1,
        osdScale: 1.8,
        osdPosition: 'TL',
        osdDuration: 900,
        osdBgColor: 'rgba(0,0,0,0.82)',
        osdTextColor: '#ffffff',
        keySpeedUp:   { code: 'Equal',     ctrlKey: false, altKey: true, shiftKey: false, metaKey: false },
        keySpeedDown: { code: 'Minus',     ctrlKey: false, altKey: true, shiftKey: false, metaKey: false },
        keyReset:     { code: 'Backspace', ctrlKey: false, altKey: true, shiftKey: false, metaKey: false },
        keyToggle:    { code: 'Backquote', ctrlKey: false, altKey: true, shiftKey: false, metaKey: false },
    };

    var POS_LABELS = { TL: '左上角', TR: '右上角', BL: '左下角', BR: '右下角', C: '居中' };
    var MIN_SPEED = 0.07;
    var MAX_SPEED = 16;
    var DEFAULT_SPEED = 1.0;

    // ═══════════════════════════════════════════
    // 配置读写
    // ═══════════════════════════════════════════
    function loadConfig() {
        var cfg = {};
        var keys = Object.keys(DEFAULTS);
        for (var i = 0; i < keys.length; i++) {
            var k = keys[i];
            var v = GM_getValue(k);
            cfg[k] = (v !== undefined && v !== null) ? v : DEFAULTS[k];
        }
        return cfg;
    }

    function saveConfig(cfg) {
        var keys = Object.keys(DEFAULTS);
        for (var i = 0; i < keys.length; i++) {
            GM_setValue(keys[i], cfg[keys[i]]);
        }
    }

    var CFG = loadConfig();

    // ═══════════════════════════════════════════
    // 状态（跨页面共享）
    // ═══════════════════════════════════════════
    var _storedSpeed = GM_getValue('_speed');
    var _storedLast = GM_getValue('_lastSpeed');
    var currentSpeed = (typeof _storedSpeed === 'number') ? _storedSpeed : DEFAULT_SPEED;
    var lastSpeed = (typeof _storedLast === 'number') ? _storedLast : DEFAULT_SPEED;
    var mediaSet = new WeakSet();

    // ═══════════════════════════════════════════
    // 工具函数
    // ═══════════════════════════════════════════
    function clamp(v, min, max) {
        return v < min ? min : v > max ? max : v;
    }

    function formatSpeed(s) {
        return s.toFixed(2).replace(/0+$/, '').replace(/\.$/, '.0');
    }

    // ── 快捷键格式化（设置面板显示用）──
    var CODE_LABELS = {
        'Equal': '=', 'Minus': '-', 'Backspace': '⌫', 'Backquote': '`',
        'Comma': ',', 'Period': '.', 'Slash': '/', 'Semicolon': ';',
        'Quote': "'", 'BracketLeft': '[', 'BracketRight': ']',
        'Backslash': '\\', 'Space': '空格', 'Delete': 'Del',
        'ArrowUp': '↑', 'ArrowDown': '↓', 'ArrowLeft': '←', 'ArrowRight': '→',
        'Home': 'Home', 'End': 'End', 'PageUp': 'PgUp', 'PageDown': 'PgDn',
        'Insert': 'Ins', 'Tab': 'Tab', 'Enter': 'Enter', 'Escape': 'Esc',
    };

    function codeLabel(code) {
        if (CODE_LABELS[code]) return CODE_LABELS[code];
        if (code.indexOf('Key') === 0) return code.slice(3);
        if (code.indexOf('Digit') === 0) return code.slice(5);
        if (code.indexOf('Numpad') === 0) return 'Num' + code.slice(6);
        return code;
    }

    function formatKey(keyCfg) {
        if (!keyCfg || !keyCfg.code) return '未设置';
        var parts = [];
        if (keyCfg.ctrlKey) parts.push('Ctrl');
        if (keyCfg.altKey) parts.push('Alt');
        if (keyCfg.shiftKey) parts.push('Shift');
        if (keyCfg.metaKey) parts.push('Meta');
        parts.push(codeLabel(keyCfg.code));
        return parts.join(' + ');
    }

    // ═══════════════════════════════════════════
    // OSD 提示
    // ═══════════════════════════════════════════
    var osdEl = null;
    var osdTimer = null;

    function osdPosStyle(pos) {
        switch (pos) {
            case 'TL': return 'top:16px;left:16px;';
            case 'TR': return 'top:16px;right:16px;';
            case 'BL': return 'bottom:16px;left:16px;';
            case 'BR': return 'bottom:16px;right:16px;';
            case 'C':  return 'top:50%;left:50%;';
            default:   return 'top:16px;left:16px;';
        }
    }

    function osdTransform(scale, pos) {
        var t = 'scale(' + scale + ')';
        if (pos === 'C') t = 'translate(-50%,-50%) ' + t;
        return t;
    }

    function ensureOSD() {
        if (osdEl) return;
        osdEl = document.createElement('div');
        osdEl.id = 'gs-osd';
        osdEl.style.cssText =
            'position:fixed;z-index:2147483647;pointer-events:none;opacity:0;' +
            'padding:' + Math.round(6 * CFG.osdScale) + 'px ' + Math.round(14 * CFG.osdScale) + 'px;' +
            'border-radius:' + Math.round(6 * CFG.osdScale) + 'px;' +
            'font:bold ' + Math.round(18 * CFG.osdScale) + 'px/1.4 system-ui,sans-serif;' +
            'background:' + CFG.osdBgColor + ';color:' + CFG.osdTextColor + ';' +
            osdPosStyle(CFG.osdPosition);
        var tryAppend = function () {
            if (document.body) {
                document.body.appendChild(osdEl);
            } else {
                requestAnimationFrame(tryAppend);
            }
        };
        tryAppend();
    }

    function rebuildOSD() {
        if (osdEl && osdEl.parentNode) {
            osdEl.parentNode.removeChild(osdEl);
        }
        osdEl = null;
        osdTimer = null;
    }

    function showOSD(text) {
        ensureOSD();
        clearTimeout(osdTimer);

        osdEl.textContent = text;
        osdEl.style.transition = 'none';
        osdEl.style.opacity = '1';
        osdEl.style.transform = osdTransform(0.95, CFG.osdPosition);

        void osdEl.offsetWidth;

        osdEl.style.transition =
            'opacity ' + CFG.osdDuration + 'ms ease-out, ' +
            'transform ' + CFG.osdDuration + 'ms ease-out';
        osdEl.style.opacity = '0';
        osdEl.style.transform = osdTransform(1.05, CFG.osdPosition);

        osdTimer = setTimeout(function () {
            if (osdEl) osdEl.style.opacity = '0';
        }, CFG.osdDuration + 100);
    }

    // ═══════════════════════════════════════════
    // 速度控制
    // ═══════════════════════════════════════════
    function setSpeedOnElement(elem, speed) {
        speed = clamp(speed, MIN_SPEED, MAX_SPEED);
        try {
            if (elem.playbackRate.toFixed(3) !== speed.toFixed(3)) {
                elem.playbackRate = speed;
            }
        } catch (e) {}
        try {
            if (elem.defaultPlaybackRate.toFixed(3) !== speed.toFixed(3)) {
                elem.defaultPlaybackRate = speed;
            }
        } catch (e) {}
    }

    function applySpeedToAll(speed) {
        speed = clamp(speed, MIN_SPEED, MAX_SPEED);
        var all = document.querySelectorAll('video, audio');
        for (var i = 0; i < all.length; i++) {
            mediaSet.add(all[i]);
            setSpeedOnElement(all[i], speed);
        }
    }

    function changeSpeed(newSpeed) {
        newSpeed = clamp(newSpeed, MIN_SPEED, MAX_SPEED);
        var step = CFG.speedStep;
        newSpeed = Math.round(newSpeed / step) * step;
        newSpeed = clamp(newSpeed, MIN_SPEED, MAX_SPEED);

        if (newSpeed !== DEFAULT_SPEED) lastSpeed = newSpeed;
        currentSpeed = newSpeed;
        // 持久化：新标签/刷新自动恢复
        GM_setValue('_speed', currentSpeed);
        GM_setValue('_lastSpeed', lastSpeed);
        applySpeedToAll(currentSpeed);
        showOSD(formatSpeed(currentSpeed) + 'x');
    }

    function speedUp()   { changeSpeed(currentSpeed + CFG.speedStep); }
    function speedDown() { changeSpeed(currentSpeed - CFG.speedStep); }
    function resetSpeed() { changeSpeed(DEFAULT_SPEED); }
    function toggleSpeed() {
        if (currentSpeed !== DEFAULT_SPEED) changeSpeed(DEFAULT_SPEED);
        else if (lastSpeed !== DEFAULT_SPEED) changeSpeed(lastSpeed);
        else changeSpeed(1.5);
    }

    // ═══════════════════════════════════════════
    // 媒体检测
    // ═══════════════════════════════════════════

    // 递归扫描元素及其 Shadow DOM 子树中的媒体
    function scanMediaIn(root) {
        if (!root) return;
        var list = root.querySelectorAll('video, audio');
        for (var i = 0; i < list.length; i++) {
            if (!mediaSet.has(list[i])) {
                mediaSet.add(list[i]);
                setSpeedOnElement(list[i], currentSpeed);
            }
        }
        // 穿透 open Shadow DOM
        var all = root.querySelectorAll('*');
        for (var j = 0; j < all.length; j++) {
            if (all[j].shadowRoot) scanMediaIn(all[j].shadowRoot);
        }
    }

    // 事件捕获：首次 play/playing/timeupdate/loadedmetadata 时登记并设速
    function handleMediaEvent(e) {
        if (!e.isTrusted) return;
        var target = e.target;
        if (target instanceof HTMLMediaElement && !mediaSet.has(target)) {
            mediaSet.add(target);
            setSpeedOnElement(target, currentSpeed);
        }
    }

    // 网站自己改了速度 → 抢回来
    function handleRateChange(e) {
        if (!e.isTrusted) return;
        var target = e.target;
        if (target instanceof HTMLMediaElement &&
            mediaSet.has(target) &&
            Math.abs(target.playbackRate - currentSpeed) > 0.001) {
            setSpeedOnElement(target, currentSpeed);
        }
    }

    function setupMediaDetection() {
        // 事件捕获
        window.addEventListener('play', handleMediaEvent, { capture: true, passive: true });
        window.addEventListener('playing', handleMediaEvent, { capture: true, passive: true });
        window.addEventListener('timeupdate', handleMediaEvent, { capture: true, passive: true });
        window.addEventListener('loadedmetadata', handleMediaEvent, { capture: true, passive: true });
        window.addEventListener('ratechange', handleRateChange, { capture: true, passive: true });

        // DOM 就绪后全量扫描
        function readyScan() {
            scanMediaIn(document);
        }
        if (document.readyState === 'loading') {
            document.addEventListener('DOMContentLoaded', readyScan);
        } else {
            readyScan();
        }

        // MutationObserver：动态插入的媒体元素
        function startMO() {
            if (!document.documentElement) {
                requestAnimationFrame(startMO);
                return;
            }
            var mo = new MutationObserver(function (muts) {
                for (var i = 0; i < muts.length; i++) {
                    var added = muts[i].addedNodes;
                    for (var j = 0; j < added.length; j++) {
                        var node = added[j];
                        if (node.nodeType === 1) { // Element
                            if (node instanceof HTMLMediaElement && !mediaSet.has(node)) {
                                mediaSet.add(node);
                                setSpeedOnElement(node, currentSpeed);
                            }
                            if (node.querySelectorAll) scanMediaIn(node);
                        }
                    }
                }
            });
            mo.observe(document.documentElement, { childList: true, subtree: true });
        }
        startMO();
    }

    // ═══════════════════════════════════════════
    // 键盘处理  ← 改为配置驱动
    // ═══════════════════════════════════════════
    function isEditable(el) {
        if (!el) return false;
        var tag = el.tagName;
        if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') return true;
        if (el.isContentEditable) return true;
        return false;
    }

    function matchKey(e, keyCfg) {
        if (!keyCfg || !keyCfg.code) return false;
        return e.code === keyCfg.code &&
            !!e.ctrlKey === !!keyCfg.ctrlKey &&
            !!e.altKey === !!keyCfg.altKey &&
            !!e.shiftKey === !!keyCfg.shiftKey &&
            !!e.metaKey === !!keyCfg.metaKey;
    }

    var KEY_ACTIONS = [
        { cfgKey: 'keySpeedUp',   fn: function () { speedUp(); } },
        { cfgKey: 'keySpeedDown', fn: function () { speedDown(); } },
        { cfgKey: 'keyReset',     fn: function () { resetSpeed(); } },
        { cfgKey: 'keyToggle',    fn: function () { toggleSpeed(); } },
    ];

    function handleKeyDown(e) {
        if (isEditable(e.target)) return;
        for (var i = 0; i < KEY_ACTIONS.length; i++) {
            if (matchKey(e, CFG[KEY_ACTIONS[i].cfgKey])) {
                KEY_ACTIONS[i].fn();
                e.preventDefault();
                e.stopPropagation();
                return;
            }
        }
    }

    // 阻止 Alt 键激活浏览器菜单栏
    function preventAltMenu(e) {
        if (e.key === 'Alt' && !e.ctrlKey && !e.shiftKey && !e.metaKey) {
            e.preventDefault();
        }
    }

    // ═══════════════════════════════════════════
    // 设置面板
    // ═══════════════════════════════════════════
    var settingsRoot = null;
    var _editCfg = null;      // 编辑中的配置副本
    var KEY_CAPTURE = null;   // 键位捕获状态

    function injectSettingsCSS() {
        if (document.getElementById('gs-settings-css')) return;
        var style = document.createElement('style');
        style.id = 'gs-settings-css';
        style.textContent =
            '#gs-settings-backdrop{position:fixed;inset:0;z-index:2147483646;' +
            'background:rgba(0,0,0,0.6);display:flex;align-items:center;justify-content:center;' +
            'font-family:system-ui,sans-serif;}' +
            '#gs-settings-panel{background:#1e1e1e;color:#e0e0e0;border-radius:12px;' +
            'padding:24px 28px;min-width:360px;max-width:440px;box-shadow:0 8px 32px rgba(0,0,0,0.5);}' +
            '#gs-settings-panel h2{margin:0 0 16px;font-size:20px;color:#fff;}' +
            '.gs-section{font-size:13px;color:#888;margin:16px 0 10px;padding-top:12px;' +
            'border-top:1px solid #333;}' +
            '.gs-section:first-of-type{margin-top:0;padding-top:0;border-top:none;}' +
            '.gs-row{display:flex;align-items:center;justify-content:space-between;' +
            'margin-bottom:12px;gap:12px;}' +
            '.gs-row label{font-size:14px;white-space:nowrap;min-width:64px;}' +
            '.gs-row input[type=range]{flex:1;height:4px;accent-color:#4a9eff;}' +
            '.gs-row .gs-val{font-size:13px;min-width:48px;text-align:right;color:#aaa;}' +
            '.gs-row select{padding:4px 8px;border-radius:4px;background:#333;color:#e0e0e0;' +
            'border:1px solid #444;font-size:13px;}' +
            '.gs-row input[type=color]{width:28px;height:28px;border:none;border-radius:4px;' +
            'background:transparent;cursor:pointer;}' +
            '.gs-key{display:inline-block;padding:3px 10px;background:#2a2a2a;border:1px solid #444;' +
            'border-radius:4px;font-size:13px;cursor:pointer;min-width:90px;text-align:center;' +
            'color:#4ac;font-family:monospace;user-select:none;}' +
            '.gs-key:hover{background:#333;border-color:#4a9eff;}' +
            '.gs-key.recording{background:#4a9eff;color:#fff;border-color:#4a9eff;' +
            'animation:gs-pulse 0.8s infinite;}' +
            '@keyframes gs-pulse{0%,100%{opacity:1}50%{opacity:0.5}}' +
            '.gs-key-empty{color:#666;}' +
            '.gs-btns{display:flex;justify-content:flex-end;gap:10px;margin-top:20px;}' +
            '.gs-btns button{padding:8px 18px;border:none;border-radius:6px;font-size:14px;cursor:pointer;}' +
            '.gs-btn-reset{background:#444;color:#ccc;}' +
            '.gs-btn-reset:hover{background:#555;}' +
            '.gs-btn-save{background:#4a9eff;color:#fff;}' +
            '.gs-btn-save:hover{background:#5ab0ff;}';
        document.head.appendChild(style);
    }

    function rgbaToHex(rgba) {
        var m = rgba.match(/[\d.]+/g);
        if (!m || m.length < 3) return '#000000';
        var r = parseInt(m[0]).toString(16).padStart(2, '0');
        var g = parseInt(m[1]).toString(16).padStart(2, '0');
        var b = parseInt(m[2]).toString(16).padStart(2, '0');
        return '#' + r + g + b;
    }

    // ── 键位捕获 ──
    function startKeyCapture(el, cfgKey) {
        if (KEY_CAPTURE) cancelKeyCapture(false);
        KEY_CAPTURE = { el: el, cfgKey: cfgKey, oldValue: _editCfg[cfgKey] };
        el.textContent = '...';
        el.classList.add('recording');
        el.classList.remove('gs-key-empty');
    }

    function cancelKeyCapture(restore) {
        if (!KEY_CAPTURE) return;
        var el = KEY_CAPTURE.el;
        var old = KEY_CAPTURE.oldValue;
        KEY_CAPTURE = null;
        el.classList.remove('recording');
        if (restore !== false) {
            el.textContent = formatKey(old);
            if (!old || !old.code) el.classList.add('gs-key-empty');
        }
    }

    function handleKeyCapture(e) {
        if (!KEY_CAPTURE) return false;
        e.preventDefault();
        e.stopPropagation();

        if (e.key === 'Escape') {
            cancelKeyCapture(true);
            return true;
        }

        // 忽略单独修饰键
        if (e.code === 'AltLeft' || e.code === 'AltRight' ||
            e.code === 'ControlLeft' || e.code === 'ControlRight' ||
            e.code === 'ShiftLeft' || e.code === 'ShiftRight' ||
            e.code === 'MetaLeft' || e.code === 'MetaRight') {
            return true;
        }

        var newKey = {
            code: e.code,
            ctrlKey: e.ctrlKey,
            altKey: e.altKey,
            shiftKey: e.shiftKey,
            metaKey: e.metaKey,
        };
        _editCfg[KEY_CAPTURE.cfgKey] = newKey;
        var el = KEY_CAPTURE.el;
        KEY_CAPTURE = null;
        el.textContent = formatKey(newKey);
        el.classList.remove('recording');
        el.classList.remove('gs-key-empty');
        return true;
    }

    function keyPickerHTML(id, cfgKey, keyCfg) {
        var cls = 'gs-key';
        if (!keyCfg || !keyCfg.code) cls += ' gs-key-empty';
        return '<span class="' + cls + '" id="gs-cfg-' + id + '" data-key="' + cfgKey + '">' +
            formatKey(keyCfg) + '</span>';
    }

    function bindKeyPickers() {
        var spans = settingsRoot.querySelectorAll('.gs-key');
        for (var i = 0; i < spans.length; i++) {
            var span = spans[i];
            var cfgKey = span.getAttribute('data-key');
            span.addEventListener('click', function (el, key) {
                return function () { startKeyCapture(el, key); };
            }(span, cfgKey));
            // 右键清除
            span.addEventListener('contextmenu', function (el, key) {
                return function (e) {
                    e.preventDefault();
                    if (KEY_CAPTURE && KEY_CAPTURE.el === el) {
                        KEY_CAPTURE = null;
                        el.classList.remove('recording');
                    }
                    _editCfg[key] = { code: '', ctrlKey: false, altKey: false, shiftKey: false, metaKey: false };
                    el.textContent = '未设置';
                    el.classList.add('gs-key-empty');
                };
            }(span, cfgKey));
        }
    }

    function openSettings() {
        if (settingsRoot) settingsRoot.remove();
        injectSettingsCSS();

        var cfg = loadConfig();

        // 深拷贝键位配置用于编辑
        _editCfg = {};
        var keyNames = ['keySpeedUp', 'keySpeedDown', 'keyReset', 'keyToggle'];
        for (var i = 0; i < keyNames.length; i++) {
            var k = keyNames[i];
            var orig = cfg[k] || DEFAULTS[k];
            _editCfg[k] = {
                code: orig.code || '',
                ctrlKey: !!orig.ctrlKey,
                altKey: !!orig.altKey,
                shiftKey: !!orig.shiftKey,
                metaKey: !!orig.metaKey,
            };
        }

        var backdrop = document.createElement('div');
        backdrop.id = 'gs-settings-backdrop';

        // prettier-ignore
        backdrop.innerHTML =
            '<div id="gs-settings-panel">' +
            '<h2>⚙ Global Speed 设置</h2>' +

            // ── OSD ──
            '<div class="gs-section">OSD 显示</div>' +

            '<div class="gs-row">' +
            '<label>大小</label>' +
            '<input type="range" id="gs-cfg-osdScale" min="0.8" max="3" step="0.1" value="' + cfg.osdScale + '">' +
            '<span class="gs-val" id="gs-val-osdScale">' + cfg.osdScale.toFixed(1) + '</span>' +
            '</div>' +

            '<div class="gs-row">' +
            '<label>位置</label>' +
            '<select id="gs-cfg-osdPosition">' +
            Object.keys(POS_LABELS).map(function(k) {
                return '<option value="' + k + '"' + (cfg.osdPosition === k ? ' selected' : '') + '>' + POS_LABELS[k] + '</option>';
            }).join('') +
            '</select>' +
            '</div>' +

            '<div class="gs-row">' +
            '<label>时长</label>' +
            '<input type="range" id="gs-cfg-osdDuration" min="300" max="3000" step="100" value="' + cfg.osdDuration + '">' +
            '<span class="gs-val" id="gs-val-osdDuration">' + cfg.osdDuration + 'ms</span>' +
            '</div>' +

            '<div class="gs-row">' +
            '<label>背景色</label>' +
            '<input type="color" id="gs-cfg-osdBgColor" value="' + rgbaToHex(cfg.osdBgColor) + '">' +
            '</div>' +

            '<div class="gs-row">' +
            '<label>文字色</label>' +
            '<input type="color" id="gs-cfg-osdTextColor" value="' + cfg.osdTextColor + '">' +
            '</div>' +

            // ── 快捷键 ──
            '<div class="gs-section">快捷键（点击修改 · 右键清除）</div>' +

            '<div class="gs-row">' +
            '<label>加速</label>' +
            keyPickerHTML('keySpeedUp', 'keySpeedUp', cfg.keySpeedUp) +
            '</div>' +

            '<div class="gs-row">' +
            '<label>减速</label>' +
            keyPickerHTML('keySpeedDown', 'keySpeedDown', cfg.keySpeedDown) +
            '</div>' +

            '<div class="gs-row">' +
            '<label>还原</label>' +
            keyPickerHTML('keyReset', 'keyReset', cfg.keyReset) +
            '</div>' +

            '<div class="gs-row">' +
            '<label>切换</label>' +
            keyPickerHTML('keyToggle', 'keyToggle', cfg.keyToggle) +
            '</div>' +

            // ── 速度 ──
            '<div class="gs-section">速度</div>' +

            '<div class="gs-row">' +
            '<label>步进</label>' +
            '<input type="range" id="gs-cfg-speedStep" min="0.05" max="0.5" step="0.05" value="' + cfg.speedStep + '">' +
            '<span class="gs-val" id="gs-val-speedStep">' + cfg.speedStep.toFixed(2) + '</span>' +
            '</div>' +

            // ── 按钮 ──
            '<div class="gs-btns">' +
            '<button class="gs-btn-reset" id="gs-btn-reset">恢复默认</button>' +
            '<button class="gs-btn-save" id="gs-btn-save">✓ 保存</button>' +
            '</div>' +
            '</div>';

        var tryAppend = function () {
            if (document.body) {
                document.body.appendChild(backdrop);
                settingsRoot = backdrop;
                bindSliders();
                bindKeyPickers();
            } else {
                requestAnimationFrame(tryAppend);
            }
        };
        tryAppend();

        backdrop.addEventListener('click', function (e) {
            if (e.target === backdrop) closeSettings();
        });

        backdrop.querySelector('#gs-btn-reset').addEventListener('click', function () {
            saveConfig(DEFAULTS);
            CFG = loadConfig();
            rebuildOSD();
            closeSettings();
            showOSD('已恢复默认');
        });

        backdrop.querySelector('#gs-btn-save').addEventListener('click', function () {
            CFG = {
                osdScale: parseFloat(backdrop.querySelector('#gs-cfg-osdScale').value),
                osdPosition: backdrop.querySelector('#gs-cfg-osdPosition').value,
                osdDuration: parseInt(backdrop.querySelector('#gs-cfg-osdDuration').value),
                speedStep: parseFloat(backdrop.querySelector('#gs-cfg-speedStep').value),
                osdBgColor: 'rgba(' +
                    parseInt(backdrop.querySelector('#gs-cfg-osdBgColor').value.slice(1,3), 16) + ',' +
                    parseInt(backdrop.querySelector('#gs-cfg-osdBgColor').value.slice(3,5), 16) + ',' +
                    parseInt(backdrop.querySelector('#gs-cfg-osdBgColor').value.slice(5,7), 16) + ',' +
                    (function(){
                        var m = CFG.osdBgColor.match(/[\d.]+$/);
                        return (m && m[0]) ? parseFloat(m[0]) : 0.82;
                    })() + ')',
                osdTextColor: backdrop.querySelector('#gs-cfg-osdTextColor').value,
                // 键位从编辑副本读取
                keySpeedUp:   _editCfg.keySpeedUp,
                keySpeedDown: _editCfg.keySpeedDown,
                keyReset:     _editCfg.keyReset,
                keyToggle:    _editCfg.keyToggle,
            };
            saveConfig(CFG);
            rebuildOSD();
            closeSettings();
            showOSD('设置已保存');
        });

        document.addEventListener('keydown', onSettingsKey, true);
    }

    function closeSettings() {
        if (settingsRoot) settingsRoot.remove();
        settingsRoot = null;
        _editCfg = null;
        KEY_CAPTURE = null;
        document.removeEventListener('keydown', onSettingsKey, true);
    }

    function onSettingsKey(e) {
        // 键位捕获优先
        if (handleKeyCapture(e)) return;
        if (e.key === 'Escape') closeSettings();
    }

    function bindSliders() {
        bindOne('osdScale', 1);
        bindOne('osdDuration', 0);
        bindOne('speedStep', 2);
    }

    function bindOne(id, decimals) {
        var slider = settingsRoot.querySelector('#gs-cfg-' + id);
        var valSpan = settingsRoot.querySelector('#gs-val-' + id);
        if (!slider || !valSpan) return;
        slider.addEventListener('input', function () {
            var v = parseFloat(this.value);
            if (id === 'osdDuration') {
                valSpan.textContent = Math.round(v) + 'ms';
            } else if (id === 'speedStep') {
                valSpan.textContent = v.toFixed(2);
            } else {
                valSpan.textContent = v.toFixed(decimals);
            }
        });
    }

    // ═══════════════════════════════════════════
    // Tampermonkey 菜单
    // ═══════════════════════════════════════════
    if (typeof GM_registerMenuCommand === 'function') {
        GM_registerMenuCommand('⚙ 设置', openSettings);
    }

    // ═══════════════════════════════════════════
    // 启动
    // ═══════════════════════════════════════════
    setupMediaDetection();
    window.addEventListener('keydown', handleKeyDown, true);
    window.addEventListener('keydown', preventAltMenu, true);
})();
