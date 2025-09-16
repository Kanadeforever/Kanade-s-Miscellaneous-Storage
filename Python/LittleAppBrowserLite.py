# -*- coding: utf-8 -*-
import sys
import argparse
import tomllib
import webview
from pathlib import Path

class API:
    def __init__(self, window, url):
        self.window = window
        self.url = url

    def toggle_fullscreen(self):
        self.window.toggle_fullscreen()

    def reload(self):
        self.window.load_url(self.url)

    def set_title(self, title: str):
        self.window.set_title(title)

def ensure_config_exists(config_path: Path):
    if config_path.exists():
        return
    try:
        lines = [
            '[App]',
            'URL = "https://example.com"',
            'Title = "默认应用"',
            'Width = 1024',
            'Height = 768',
            'Fullscreen = false',
            'OnTop = false',
            'RememberSize = true',
            '',
            '[Hotkeys]',
            'Fullscreen = "F11"',
            'Reload = "Ctrl+R"',
            'ZoomIn = "Ctrl+Plus"',
            'ZoomOut = "Ctrl+Minus"',
            'ZoomReset = "Ctrl+0"'
        ]
        config_path.write_text('\n'.join(lines), encoding='utf-8')
        print(f"首次运行：已生成默认配置文件 → {config_path}")
    except Exception as e:
        sys.exit(f"Error: 无法创建默认配置文件：{e}")

def inject_load_indicator(window, original_title: str):
    js = f"""
    (function() {{
        const orig = "{original_title.replace('"', '\\"')}";
        // 导航开始：页面卸载前触发
        window.addEventListener('beforeunload', function() {{
            window.pywebview.api.set_title("⏳ 加载中… " + orig);
        }});
        // 导航完成：DOM 全部就绪后触发
        window.addEventListener('load', function() {{
            window.pywebview.api.set_title(orig);
        }});
    }})();
    """
    window.evaluate_js(js)

def generate_hotkey_js(hotkeys: dict) -> str:
    js_lines = ["document.addEventListener('keydown', function(e) {"]

    def match(key_combo):
        parts = key_combo.upper().split('+')
        checks = []
        if 'CTRL' in parts:
            checks.append("e.ctrlKey")
        if 'ALT' in parts:
            checks.append("e.altKey")
        if 'SHIFT' in parts:
            checks.append("e.shiftKey")
        key = next((p for p in parts if p not in ['CTRL', 'ALT', 'SHIFT']), None)
        if key == 'PLUS':
            key = '+'
        elif key == 'MINUS':
            key = '-'
        elif key == '0':
            key = '0'
        elif key == 'F11':
            return "if (e.key === 'F11')"
        return f"if ({' && '.join(checks)} && e.key.toLowerCase() === '{key.lower()}')"

    for action, combo in hotkeys.items():
        condition = match(combo)
        if action == 'Fullscreen':
            js_lines.append(f"{condition} {{ window.pywebview.api.toggle_fullscreen(); e.preventDefault(); }}")
        elif action == 'Reload':
            js_lines.append(f"{condition} {{ window.pywebview.api.reload(); e.preventDefault(); }}")
            # js_lines.append(f"{condition} {{ window.location.href = window.location.href; e.preventDefault(); }}")
        elif action == 'ZoomIn':
            js_lines.append(f"{condition} {{ let z=parseFloat(document.body.style.zoom)||1; document.body.style.zoom=z+0.1; e.preventDefault(); }}")
        elif action == 'ZoomOut':
            js_lines.append(f"{condition} {{ let z=parseFloat(document.body.style.zoom)||1; document.body.style.zoom=Math.max(z-0.1,0.1); e.preventDefault(); }}")
        elif action == 'ZoomReset':
            js_lines.append(f"{condition} {{ document.body.style.zoom=1; e.preventDefault(); }}")

    js_lines.append("});")
    return '\n'.join(js_lines)

def inject_hotkeys(window, hotkeys):
    js_code = generate_hotkey_js(hotkeys)
    window.evaluate_js(js_code)

def inject_context_menu(window, home_url: str, hotkeys: dict):
    # 构造 JS 中的菜单项数组，自动映射到你的 hotkeys
    js_items = []
    # 固定的导航项
    js_items.append("{ label: '后退',    action: () => history.back() }")
    js_items.append("{ label: '前进',    action: () => history.forward() }")
    js_items.append(f"{{ label: '主页',    action: () => location.href = '{home_url}' }}")

    # 动态映射热键动作
    for action, combo in hotkeys.items():
        if action == 'Fullscreen':
            js_action = "window.pywebview.api.toggle_fullscreen()"
        elif action == 'Reload':
            js_action = "window.pywebview.api.reload()"
        elif action == 'ZoomIn':
            js_action = "let z = parseFloat(document.body.style.zoom) || 1; document.body.style.zoom = z + 0.1"
        elif action == 'ZoomOut':
            js_action = "let z = parseFloat(document.body.style.zoom) || 1; document.body.style.zoom = Math.max(z - 0.1, 0.1)"
        elif action == 'ZoomReset':
            js_action = "document.body.style.zoom = 1"
        else:
            continue

        # 菜单标签里加上组合键提示
        label = f"{action} ({combo})"
        js_items.append(
            "{ label: '%s', action: function() { %s; hide(); } }" % (label, js_action)
        )

    # 最终注入的 JS
    js = f"""
    (function() {{
        let menu = null;
        function hide() {{
            if (menu) {{ document.body.removeChild(menu); menu = null; }}
        }}
        document.addEventListener('contextmenu', function(e) {{
            e.preventDefault();
            hide();
            menu = document.createElement('div');
            Object.assign(menu.style, {{
                position: 'fixed',
                top: e.clientY + 'px',
                left: e.clientX + 'px',
                background: '#fff',
                border: '1px solid #ccc',
                boxShadow: '2px 2px 6px rgba(0,0,0,0.2)',
                zIndex: 9999,
                padding: '4px 0',
                fontSize: '14px',        // 锁定字体大小
                lineHeight: '1.5',       // 锁定行高
                userSelect: 'none'
            }});

            const items = [{','.join(js_items)}];
            items.forEach(item => {{
                const el = document.createElement('div');
                el.className = 'pywebview-context-item';
                el.innerText = item.label;
                Object.assign(el.style, {{
                    padding: '4px 16px',
                    cursor: 'default',
                    fontSize: 'inherit',     // 继承容器 font-size
                    lineHeight: 'inherit'
                }});
                el.onmouseenter = () => el.style.background = '#f0f0f0';
                el.onmouseleave = () => el.style.background = '';
                el.onclick = () => item.action();
                menu.appendChild(el);
            }});

            document.body.appendChild(menu);
        }});
        document.addEventListener('click', hide);
    }})();"""
    window.evaluate_js(js)

def parse_args():
    parser = argparse.ArgumentParser(
        description="启动 WebView 应用（TOML + 快捷键 + 全屏支持）"
    )
    parser.add_argument(
        '-c', '--config',
        type=Path,
        help="指定 .toml 配置文件，默认与脚本同名"
    )
    return parser.parse_args()

def load_config(path: Path) -> tuple[dict, dict]:
    with path.open('rb') as fp:
        data = tomllib.load(fp)
    app = data.get('App')
    if not app or not app.get('URL'):
        sys.exit(f"Error: [{path.name}] 中的 [App].URL 为必填项")
    hotkeys = data.get('Hotkeys', {})
    return app, hotkeys

def main():
    args = parse_args()
    config_path = args.config or Path(sys.argv[0]).with_suffix('.toml')
    ensure_config_exists(config_path)

    if not config_path.exists():
        sys.exit(f"Error: 找不到配置文件：{config_path}")

    app, hotkeys = load_config(config_path)

    url        = app['URL']
    title      = app.get('Title', 'Web App')
    width      = int(app.get('Width', 1024))
    height     = int(app.get('Height', 768))
    fullscreen = bool(app.get('Fullscreen', False))
    on_top     = bool(app.get('OnTop', False))
    original_title = title

    window = webview.create_window(
        title=original_title,
        url=url,
        width=width,
        height=height,
        fullscreen=fullscreen,
        on_top=on_top
    )

    api = API(window, url)
    window.expose(
        api.toggle_fullscreen,
        api.reload,
        api.set_title
    )

    # 每次页面加载完成后重新注入快捷键
    def on_loaded(window):
        inject_hotkeys(window, hotkeys)
        inject_context_menu(window, url, hotkeys)
        inject_load_indicator(window, original_title)

    window.events.loaded += on_loaded
    webview.start()

if __name__ == '__main__':
    main()
