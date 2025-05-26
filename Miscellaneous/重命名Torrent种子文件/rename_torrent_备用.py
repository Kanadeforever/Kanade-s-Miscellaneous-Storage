import os
import sys
import bencodepy
import re

def extract_file_names(torrent_path):
    try:
        with open(torrent_path, 'rb') as f:
            torrent_data = bencodepy.decode(f.read())
            if b'info' in torrent_data:
                info = torrent_data[b'info']
                if b'files' in info:
                    # 处理包含多个文件的情况
                    return [file[b'path'][0].decode('utf-8') for file in info[b'files']]
                elif b'name' in info:
                    # 处理包含单个文件的情况
                    return [info[b'name'].decode('utf-8')]
                else:
                    # 解析 name 和 piece 之间的文件名
                    data = f.read().decode('utf-8')
                    match = re.search(r'name\d{1,4}:(.+?)piece', data)
                    if match:
                        name = match.group(1).strip()
                        return [name]
    except Exception as e:
        print(f"读取 {torrent_path} 时出错：{e}")
    return []

def rename_torrent_file(torrent_path):
    file_names = extract_file_names(torrent_path)
    if not file_names:
        print(f"未找到有效文件名：{torrent_path}")
        return
    
    # 使用第一个文件名来重命名 torrent 文件
    new_torrent_name = os.path.join(os.path.dirname(torrent_path), file_names[0] + '.torrent')
    if not os.path.exists(new_torrent_name):
        os.rename(torrent_path, new_torrent_name)
        print(f"已将 {torrent_path} 重命名为 {new_torrent_name}")
    else:
        print(f"文件名冲突：{new_torrent_name} 已存在。{torrent_path} 未重命名。")

if __name__ == '__main__':
    if len(sys.argv) != 2:
        print("用法：python rename_torrent.py <torrent 文件的路径>")
        sys.exit(1)
    
    torrent_path = sys.argv[1]
    rename_torrent_file(torrent_path)
