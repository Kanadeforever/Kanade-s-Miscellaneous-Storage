#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
批量检测视频文件的深度解码完整性。
使用多个 ffmpeg 进程并发检测，每个进程可配置线程数。
输出详细的 CSV 报告。
"""

import os, sys, subprocess, time, csv, argparse, logging, multiprocessing as mp
from tqdm import tqdm
from datetime import datetime

def init_logger(logfile):
    logging.basicConfig(
        level=logging.INFO,
        format='[%(asctime)s] %(levelname)s: %(message)s',
        handlers=[
            logging.FileHandler(logfile, encoding='utf-8'),
            logging.StreamHandler(sys.stdout)
        ]
    )

def parse_args():
    cpu_count = mp.cpu_count()
    parser = argparse.ArgumentParser(
        description='使用 ffmpeg 深度解码检测视频文件是否完整'
    )
    parser.add_argument('-d', '--directory', required=True, help='待检测的视频文件夹路径')
    parser.add_argument('-e', '--extensions', default='mp4,mkv,avi,flv,wmv,webm,mov,m4v,ts,mts,m2ts,3gp,3g2,f4v,asf,vob,mpg,mpeg,rm,rmvb,dv,ogv', help='要检测的文件后缀，多个用逗号分隔（默认: mp4,mkv,avi,flv,wmv,webm,mov,m4v,ts,mts,m2ts,3gp,3g2,f4v,asf,vob,mpg,mpeg,rm,rmvb,dv,ogv）')
    parser.add_argument('-w', '--workers', type=int, default=None, help=f'并发运行的 ffmpeg 进程数（默认: CPU 核心数的一半，范围: 1 到 {cpu_count}）')
    parser.add_argument('-T', '--threads', type=int, default=1, help='每个 ffmpeg 实例使用的线程数（默认: 1）')
    parser.add_argument('-t', '--timeout', type=int, default=300, help='每个文件的解码超时时间（秒，默认: 300）')
    parser.add_argument('-l', '--logfile', default='decode_report.log', help='日志文件路径（默认: decode_report.log）')
    return parser.parse_args()

def find_videos(root, exts):
    for dirpath, _, filenames in os.walk(root):
        for fn in filenames:
            if fn.lower().endswith(exts):
                yield os.path.join(dirpath, fn)

def classify_error(err_text):
    if 'Invalid' in err_text or 'corrupt' in err_text:
        return '格式错误'
    elif 'stream' in err_text:
        return '流错误'
    elif 'timeout' in err_text.lower():
        return '超时'
    elif err_text:
        return '解码错误'
    return None

def check_file(args):
    file_path, timeout, threads = args
    cmd = [
        'ffmpeg',
        '-v', 'error',
        '-threads', str(threads),
        '-i', file_path,
        '-f', 'null', '-'
    ]
    start_time = time.time()
    try:
        proc = subprocess.run(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            timeout=timeout
        )
        duration = round(time.time() - start_time, 2)
        raw = proc.stderr or b''
        err_text = raw.decode('utf-8', errors='ignore').strip()
        if proc.returncode != 0 or err_text:
            return {
                '文件路径': file_path,
                '状态': '损坏',
                '错误信息': err_text,
                '错误类型': classify_error(err_text),
                '耗时（秒）': duration
            }
        return {
            '文件路径': file_path,
            '状态': '完整',
            '错误信息': '',
            '错误类型': '',
            '耗时（秒）': duration
        }
    except subprocess.TimeoutExpired:
        duration = round(time.time() - start_time, 2)
        return {
            '文件路径': file_path,
            '状态': '超时',
            '错误信息': f'超时（超过 {timeout} 秒）',
            '错误类型': '超时',
            '耗时（秒）': duration
        }

def main():
    args = parse_args()

    max_cores = mp.cpu_count()
    if args.workers is None:
        args.workers = max(1, max_cores // 2)
    else:
        args.workers = max(1, min(args.workers, max_cores))

    init_logger(args.logfile)
    exts = tuple('.' + e.strip().lower() for e in args.extensions.split(','))
    videos = list(find_videos(args.directory, exts))

    logging.info(f'共发现 {len(videos)} 个视频文件')
    logging.info(f'使用 {args.workers} 个并发进程，每个 ffmpeg 使用 {args.threads} 个线程')

    pool = mp.Pool(processes=args.workers)
    tasks = ((f, args.timeout, args.threads) for f in videos)

    results = []
    for result in tqdm(pool.imap_unordered(check_file, tasks), total=len(videos), desc='正在检测'):
        results.append(result)
        if result['状态'] == '完整':
            logging.info(f'完整: {result["文件路径"]}')
        else:
            logging.error(f'{result["状态"]}: {result["文件路径"]} | 错误信息: {result["错误信息"]}')

    pool.close()
    pool.join()

    # 输出 CSV 报告
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    report_path = f'decode_report_{timestamp}.csv'
    with open(report_path, 'w', encoding='utf-8-sig', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=[
            '文件路径', '状态', '错误信息', '错误类型', '耗时（秒）'
        ])
        writer.writeheader()
        writer.writerows(results)

    logging.info(f'全部检测完成，报告已保存至: {report_path}')

if __name__ == '__main__':
    main()
