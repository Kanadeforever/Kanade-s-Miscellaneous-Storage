import os
import sys
import bencodepy

def extract_file_names(torrent_path):
    with open(torrent_path, 'rb') as f:
        torrent_data = bencodepy.decode(f.read())
        if b'info' in torrent_data and b'files' in torrent_data[b'info']:
            return [file[b'path'][0].decode('utf-8') for file in torrent_data[b'info'][b'files']]
        elif b'info' in torrent_data and b'name' in torrent_data[b'info']:
            return [torrent_data[b'info'][b'name'].decode('utf-8')]
        else:
            return []

def rename_torrent_file(torrent_path):
    file_names = extract_file_names(torrent_path)
    if not file_names:
        print(f"未找到有效文件名：{torrent_path}")
        return
    
    new_torrent_name = os.path.join(os.path.dirname(torrent_path), file_names[0] + '.torrent')
    os.rename(torrent_path, new_torrent_name)
    print(f"已将 {torrent_path} 重命名为 {new_torrent_name}")

if __name__ == '__main__':
    if len(sys.argv) != 2:
        print("用法：python rename_torrent.py <torrent 文件的路径>")
        sys.exit(1)
    
    torrent_path = sys.argv[1]
    rename_torrent_file(torrent_path)
