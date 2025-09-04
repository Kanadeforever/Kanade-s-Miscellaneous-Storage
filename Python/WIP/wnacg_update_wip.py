#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import sys
import csv
import time
import re
import configparser
import logging
import requests
from urllib.parse import urljoin, urlparse
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

# ====== 全局配置 ======
DEFAULT_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)',
}

# ------------------------
# Logging Setup
# ------------------------
BASE_DIR    = os.path.dirname(os.path.abspath(__file__))
SCRIPT_NAME = os.path.splitext(os.path.basename(__file__))[0]
LOG_PATH    = os.path.join(BASE_DIR, f"{SCRIPT_NAME}.log")

logger = logging.getLogger(SCRIPT_NAME)
logger.setLevel(logging.DEBUG)

# File handler: DEBUG 及以上
fh = logging.FileHandler(LOG_PATH, encoding='utf-8')
fh.setLevel(logging.DEBUG)
fh.setFormatter(logging.Formatter(
    '%(asctime)s [%(levelname)s] %(name)s: %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
))
logger.addHandler(fh)

# Console handler: INFO 及以上
ch = logging.StreamHandler(sys.stdout)
ch.setLevel(logging.INFO)
ch.setFormatter(logging.Formatter(
    '%(asctime)s %(message)s', datefmt='%H:%M:%S'
))
logger.addHandler(ch)

# ------------------------
# Utility Functions
# ------------------------
def prompt_and_exit(msg: str):
    logger.error(msg)
    input("按任意键退出脚本...")
    sys.exit(1)

def safe_filename(name: str) -> str:
    return re.sub(r'[\/:*?"<>|]', '_', name)

def get_soup(url: str, headers=None, timeout=10) -> BeautifulSoup:
    logger.debug(f"请求 URL：{url}")
    resp = requests.get(url, headers=headers or {}, timeout=timeout)
    resp.raise_for_status()
    resp.encoding = resp.apparent_encoding
    return BeautifulSoup(resp.text, 'html.parser')

# ------------------------
# 初始化 download.csv 与 INI
# ------------------------
DOWNLOAD_CSV = os.path.join(BASE_DIR, 'download.csv')
INI_PATH     = os.path.join(BASE_DIR, f"{SCRIPT_NAME}.ini")

logger.info("===== 脚本启动 =====")

need_exit = False
if not os.path.exists(DOWNLOAD_CSV) or os.stat(DOWNLOAD_CSV).st_size < 3:
    logger.warning("未找到有效的 download.csv，正在创建模板。")
    with open(DOWNLOAD_CSV, 'w', newline='', encoding='utf-8-sig') as f:
        csv.writer(f).writerow(['name', 'url'])
    logger.info("已创建 download.csv，请填写后重试。")
    need_exit = True
else:
    logger.info("找到 download.csv，准备读取任务列表。")

config = configparser.ConfigParser()
if not os.path.exists(INI_PATH):
    logger.warning(f"未找到 {SCRIPT_NAME}.ini，正在创建模板。")
    config['settings'] = {
        'url':          '',
        'dl_folder':    '',
        'dl_interval':  '1.0',
        'wait_timeout': '60',
        'proxy':        ''
    }
    with open(INI_PATH, 'w', encoding='utf-8') as f:
        f.write(
            "# 配置示例:\n"
            "# proxy = http://127.0.0.1:1080\n"
            "# socks5 用法: socks5://127.0.0.1:1080\n"
        )
        config.write(f)
    logger.info(f"已创建 {SCRIPT_NAME}.ini，请填写后重试。")
    need_exit = True
else:
    try:
        config.read(INI_PATH, encoding='utf-8')
        cfg = config['settings']
        _ = cfg['url']; _ = cfg['dl_interval']; _ = cfg['wait_timeout']; _ = cfg['proxy']
        logger.info(f"读取 {SCRIPT_NAME}.ini 成功。")
    except Exception as e:
        prompt_and_exit(f"读取 ini 文件出错 ({e})，请删除后重试。")

if need_exit:
    logger.info("脚本初始化未完成，退出。")
    input("按任意键结束脚本...")
    sys.exit(0)

PREFIX_URL   = cfg['url'].strip()
DL_FOLDER    = cfg['dl_folder'].strip() or BASE_DIR
DL_INTERVAL  = float(cfg['dl_interval'].strip() or 1.0)
WAIT_TIMEOUT = int(cfg['wait_timeout'].strip() or 60)
PROXY        = cfg['proxy'].strip()

os.makedirs(DL_FOLDER, exist_ok=True)
logger.debug(
    f"配置项：PREFIX_URL={PREFIX_URL}, DL_FOLDER={DL_FOLDER}, "
    f"DL_INTERVAL={DL_INTERVAL}, WAIT_TIMEOUT={WAIT_TIMEOUT}, PROXY={PROXY}"
)

# ------------------------
# 核心流程
# ------------------------
def process_series(browser, session, series_name: str, list_url: str):
    logger.info(f"Start series: {series_name} -> {list_url}")
    safe_name  = safe_filename(series_name)
    series_csv = os.path.join(BASE_DIR, f"{safe_name}.csv")
    headers    = ['title', 'href', 'info_col', 'count']

    # 1. 写 CSV 表头
    with open(series_csv, 'w', newline='', encoding='utf-8-sig') as f:
        csv.writer(f).writerow(headers)

    # 2. 抓取列表页
    all_rows, page_url = [], list_url
    while page_url:
        logger.info(f"抓取列表页：{page_url}")
        try:
            soup = get_soup(page_url, headers=DEFAULT_HEADERS)
        except Exception as e:
            logger.error(f"列表页 {page_url} 抓取失败：{e}")
            break

        container = soup.select_one('div.grid > div.gallary_wrap > ul.cc')
        if not container:
            logger.warning("未找到<ul.cc>容器，停止抓取。")
            break

        for li in container.select('li.li.gallary_item'):
            a_tag    = li.find('a')
            title    = (a_tag.get('title') or a_tag.text or '').strip() if a_tag else ''
            raw_href = a_tag.get('href','').strip() if a_tag else ''
            full     = urljoin(PREFIX_URL, raw_href).replace('index-aid-', 'list-aid-')
            info_div = li.find('div', class_='info_col')
            raw_info = ''.join(info_div.stripped_strings) if info_div else ''
            m        = re.search(r'(\d+)張照片，創建於(\d{4}-\d{2}-\d{2})', raw_info)
            info_col = f"{m.group(1)}pics_{m.group(2)}" if m else ''
            all_rows.append({
                'title':    title,
                'href':     full,
                'info_col': info_col,
                'count':    ''
            })

        # 翻页
        pag = soup.select_one('div.bot_toolbar.cc div.f_left.paginator')
        if pag and (nxt := pag.find('span', class_='next')):
            sib = nxt.find_next_sibling('a')
            page_url = urljoin(list_url, sib['href']) if sib and sib.get('href') else None
            logger.debug(f"下一页：{page_url}")
        else:
            logger.info("无下一页，列表抓取结束。")
            break

    # 3. 写入初版列表
    with open(series_csv, 'a', newline='', encoding='utf-8-sig') as f:
        writer = csv.DictWriter(f, fieldnames=headers)
        for row in all_rows:
            writer.writerow(row)
    logger.info(f"{series_csv} 列表信息写入完成，共 {len(all_rows)} 条。")

    # 4. 创建下载目录
    series_folder = os.path.join(DL_FOLDER, safe_name)
    os.makedirs(series_folder, exist_ok=True)
    logger.info(f"创建系列目录：{series_folder}")

    # 5. 章节循环：Playwright 用于渲染 & 捕获图片 URL，requests 用于下载
    for row in all_rows:
        chap_title, chap_href = row['title'], row['href']
        chap_folder = os.path.join(series_folder, safe_filename(chap_title))
        os.makedirs(chap_folder, exist_ok=True)
        logger.info(f"处理章节：{chap_title} -> {chap_href}")

        # Playwright 新页面 & 网络监听
        page = browser.new_page()
        if PROXY:
            page.context.set_default_navigation_timeout(WAIT_TIMEOUT*1000)
        image_urls = []
        page.on('response', lambda resp: image_urls.append(resp.url)
                if resp.url.lower().endswith(('.jpg','.jpeg','.png','.gif')) else None)

        # 打开 & 等待
        try:
            page.goto(chap_href, timeout=WAIT_TIMEOUT*1000)
            page.reload(timeout=WAIT_TIMEOUT*1000)
            page.wait_for_selector('#img_list img', timeout=WAIT_TIMEOUT*1000)
        except PlaywrightTimeoutError:
            logger.error(f"{chap_title} 加载超时，跳过本章节")
            page.close()
            continue
        except Exception as e:
            logger.error(f"{chap_title} 页面打开出错：{e}")
            page.close()
            continue

        # 去重 & 下载
        unique_urls = []
        for url in image_urls:
            if url not in unique_urls:
                unique_urls.append(url)
        logger.info(f"捕获到 {len(unique_urls)} 张图片，开始下载")

        total = 0
        for img_url in unique_urls:
            src = img_url
            if src.startswith('//'):
                src = 'https:' + src
            elif src.startswith('/'):
                src = urljoin(chap_href, src)

            fname = os.path.basename(urlparse(src).path)
            dst   = os.path.join(chap_folder, fname)

            # 已下载则跳过
            if os.path.exists(dst):
                logger.info(f"{fname} 已存在，跳过")
                continue

            try:
                dl = session.get(
                    src,
                    headers={'Referer': chap_href},
                    stream=True,
                    timeout=15
                )
                dl.raise_for_status()
                with open(dst, 'wb') as wf:
                    for chunk in dl.iter_content(1024*16):
                        wf.write(chunk)
                total += 1
                logger.debug(f"已下载：{fname}")
            except Exception as e:
                logger.error(f"下载失败 {src}：{e}")
            time.sleep(DL_INTERVAL)

        row['count'] = str(total)
        logger.info(f"章节完成：{chap_title}，共下载 {total} 张图片")
        page.close()

    # 6. 回写最终 count 和 info_col
    with open(series_csv, 'w', newline='', encoding='utf-8-sig') as f:
        writer = csv.DictWriter(f, fieldnames=headers)
        writer.writeheader()
        for row in all_rows:
            writer.writerow(row)

    logger.info(f"Series 完成：{series_name}，详情见 {series_csv}")


def main():
    # requests Session with proxy
    session = requests.Session()
    session.headers.update(DEFAULT_HEADERS)
    if PROXY:
        session.proxies.update({
            'http':  PROXY,
            'https': PROXY
        })
        logger.info(f"requests 使用代理：{PROXY}")

    # Playwright 启动
    with sync_playwright() as p:
        launch_opts = {'headless': True}
        if PROXY:
            launch_opts['proxy'] = {'server': PROXY}
        browser = p.chromium.launch(**launch_opts)
        logger.info("Playwright 浏览器已启动")

        logger.info("开始读取 download.csv")
        with open(DOWNLOAD_CSV, newline='', encoding='utf-8-sig') as f:
            reader = csv.DictReader(f)
            for idx, line in enumerate(reader, 1):
                name, url = line['name'].strip(), line['url'].strip()
                if not name or not url:
                    logger.warning(f"第 {idx} 行无效，跳过")
                    continue
                process_series(browser, session, name, url)

        browser.close()
        logger.info("Playwright 浏览器已关闭")

    logger.info("所有任务完成，脚本结束。")


if __name__ == '__main__':
    main()